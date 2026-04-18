"""
AlienCore - lhm_manager.py
Manages the lhm_bridge .NET daemon process.

The bridge (tools/lhm_bridge/dist/lhm_bridge.exe) runs as a persistent daemon:
  - computer.Open() fires once at launch (expensive — happens only at startup)
  - AlienCore writes a newline to its stdin to request a poll
  - The bridge responds with one compact JSON line on stdout
  - On EOF (stdin closed), the bridge calls computer.Close() and exits

This eliminates the .NET CLR startup cost that previously caused CPU spikes
every poll cycle.  The bridge process lives for the duration of AlienCore.
"""

import json
import logging
import os
import queue
import subprocess
import threading
import time

logger = logging.getLogger("aliencore.lhm")

_BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BRIDGE_EXE = os.path.join(_BASE_DIR, "tools", "lhm_bridge", "dist", "lhm_bridge.exe")

# ── daemon state ──────────────────────────────────────────────────────────────
_lock          = threading.Lock()
_proc          = None   # subprocess.Popen or None
_fail_count    = 0      # consecutive poll failures since last success
_ever_succeeded = False

# ── restart backoff ───────────────────────────────────────────────────────────
# Repeated CLR + hardware.Open() launches every 2s cause visible CPU spikes.
# After each failure, double the wait before allowing the next restart.
# Reset to 0 on first success so future crashes re-enter the ramp from scratch.
_last_kill_time:    float = 0.0
_restart_delay_secs: float = 0.0
_BACKOFF_MIN  = 5.0    # minimum wait after a failure (seconds)
_BACKOFF_MAX  = 60.0   # cap — retry at least once per minute


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_sensors() -> list:
    """
    Request one sensor snapshot from the persistent bridge daemon.
    Writes a newline to the bridge's stdin; reads one JSON line from stdout.
    Starts (or restarts) the bridge process automatically if needed.
    Returns empty list on any error.
    """
    global _fail_count, _ever_succeeded

    exe = _bridge_exe_path()
    if not exe:
        _log_failure("lhm_bridge.exe not found at expected path")
        return []

    # Backoff: don't spawn a new CLR process until the delay has passed.
    # Each failure doubles the wait; success resets it to zero.
    if _last_kill_time > 0:
        elapsed = time.time() - _last_kill_time
        if elapsed < _restart_delay_secs:
            return []   # still in back-off window

    with _lock:
        proc = _ensure_running(exe)
        if proc is None:
            return []

        try:
            proc.stdin.write(b"\n")
            proc.stdin.flush()
            raw = _readline_timeout(proc.stdout, timeout=15)
            if raw is None:
                # Timeout — bridge is hanging; kill and enter backoff
                _kill_proc()
                _log_failure("bridge timed out (no response within 15 s)")
                return []
            if not raw:
                # EOF from bridge — it crashed; will restart next call
                _kill_proc()
                _log_failure("bridge sent EOF (process may have crashed)")
                return []
            sensors = json.loads(raw.decode("utf-8"))
            _fail_count     = 0
            _ever_succeeded = True
            _reset_backoff()
            return sensors

        except json.JSONDecodeError as e:
            _log_failure(f"bridge output was not valid JSON: {e}")
            _kill_proc()
            return []
        except OSError as e:
            _log_failure(f"pipe I/O error: {e}")
            _kill_proc()
            return []
        except Exception as e:
            _log_failure(f"poll failed: {e}")
            _kill_proc()
            return []


def stop():
    """Gracefully close the bridge daemon by closing its stdin."""
    global _proc
    with _lock:
        _kill_proc()
    logger.info("lhm_bridge daemon stopped.")


def bridge_exe_path() -> str:
    """Return the resolved bridge exe path (may not exist yet)."""
    return _bridge_exe_path() or _BRIDGE_EXE


# ─────────────────────────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_running(exe: str):
    """
    Return the running bridge Popen, starting it if necessary.
    Must be called while _lock is held.  Returns None on failure.
    """
    global _proc
    if _proc is not None and _proc.poll() is None:
        return _proc   # already running
    if _proc is not None:
        logger.warning("lhm_bridge exited (code %d) — restarting", _proc.returncode)
        _proc = None

    try:
        env = os.environ.copy()
        env["LHM_DIR"] = os.path.dirname(exe)
        _proc = subprocess.Popen(
            [exe],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
            env=env,
        )
        logger.info("lhm_bridge daemon started (pid %d)", _proc.pid)
        # Give the .NET CLR + LibreHardwareMonitor computer.Open() time to initialize.
        # 2 s is the minimum; hardware with many sensors can take up to ~5 s.
        time.sleep(2.0)
        return _proc
    except FileNotFoundError:
        _log_failure(f"bridge exe missing: {exe}")
        return None
    except Exception as e:
        _log_failure(f"failed to start bridge: {e}")
        return None


def _readline_timeout(stream, timeout: float) -> bytes | None:
    """Read one line from *stream* with a wall-clock timeout.

    Returns the raw bytes (possibly empty on EOF), or None on timeout.
    Uses a daemon thread because Windows pipes don't support select().
    """
    q: queue.Queue = queue.Queue()

    def _reader():
        try:
            q.put(stream.readline())
        except Exception:
            q.put(b"")

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return None   # timed out


def _kill_proc():
    """Terminate the bridge process. Must be called while _lock is held.

    Kept short (≤0.3 s) so application shutdown doesn't stall — the bridge
    is an external .NET subprocess and a hard kill is safe: we never read
    stdout after stdin EOF, and the process owns no shared resources.
    """
    global _proc, _fail_count, _last_kill_time, _restart_delay_secs
    if _proc is None:
        return
    # Close stdin first so the bridge sees EOF and exits cleanly.
    try:
        _proc.stdin.close()
    except Exception:
        pass
    try:
        _proc.wait(timeout=0.3)
    except Exception:
        pass
    try:
        _proc.kill()
    except Exception:
        pass
    # Now that the process is gone, stderr is closed — read1() returns immediately.
    # Reading BEFORE kill blocks forever on a healthy bridge with an empty stderr.
    try:
        err_bytes = _proc.stderr.read1(4096) if hasattr(_proc.stderr, "read1") else b""
        if err_bytes:
            logger.warning("lhm_bridge stderr: %s", err_bytes.decode("utf-8", errors="replace").strip())
    except Exception:
        pass
    _proc = None
    _fail_count += 1
    _last_kill_time = time.time()
    _restart_delay_secs = _BACKOFF_MIN if _restart_delay_secs < _BACKOFF_MIN \
        else min(_BACKOFF_MAX, _restart_delay_secs * 2)


def _reset_backoff():
    """Called on successful poll — resets restart backoff so future crashes start fresh."""
    global _restart_delay_secs, _last_kill_time
    _restart_delay_secs = 0.0
    _last_kill_time     = 0.0


def _log_failure(msg: str):
    """Log bridge failures at WARNING for the first hit and every 10th; DEBUG otherwise."""
    if _fail_count == 0 or _fail_count % 10 == 0:
        logger.warning("lhm_bridge: %s (consecutive failures: %d)", msg, _fail_count + 1)
    else:
        logger.debug("lhm_bridge: %s", msg)


def _bridge_exe_path() -> str:
    """Return path to bridge exe if it exists, else empty string."""
    from core import config_manager as cfg
    c = cfg.get()
    override = c.get("lhm", {}).get("bridge_exe", "")
    path = override if override else _BRIDGE_EXE
    return path if os.path.exists(path) else ""
