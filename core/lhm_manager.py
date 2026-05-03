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

from core.constants import BASE_DIR as _BASE_DIR

logger = logging.getLogger("aliencore.lhm")

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
#
# The first _BACKOFF_GRACE failures retry immediately — under all-core CPU
# stress the bridge can miss a poll or two purely from scheduler contention
# even though it's healthy, and we don't want a single transient miss to lock
# readings out for a minute.
_last_kill_time:    float = 0.0
_restart_delay_secs: float = 0.0
_BACKOFF_GRACE = 3     # first N failures skip the delay entirely
_BACKOFF_MIN   = 5.0   # minimum wait after the grace period (seconds)
_BACKOFF_MAX   = 30.0  # cap — retry at least once per 30 s


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

    with _lock:
        # Backoff: don't spawn a new CLR process until the delay has passed.
        # Each failure doubles the wait; success resets it to zero.
        # Check is inside the lock so two racing threads can't both pass
        # the elapsed check and simultaneously spawn a new bridge process.
        if _last_kill_time > 0:
            elapsed = time.time() - _last_kill_time
            if elapsed < _restart_delay_secs:
                return []   # still in back-off window

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


def prewarm() -> None:
    """Start the bridge daemon on a background thread so its cold-start cost
    (subprocess spawn + .NET CLR init + LibreHardwareMonitor computer.Open)
    runs in parallel with the rest of AlienCore startup.  Without this, the
    SensorThread's first poll absorbs the full 2-3 s warmup and the sensor bar
    shows "---" cells for several seconds after launch.

    Idempotent and non-blocking: returns immediately, swallows all errors.
    Safe to call before sensors.start(); both paths share _lock so the first
    real poll just sees the proc already running."""
    def _worker():
        try:
            # Multiple discarded polls drive several LHM .Update() cycles so
            # sensors that report zero on the first read (AMD CPU Tdie, NVMe
            # Composite — LHM's NVMe driver issues async SMART queries whose
            # results only land on a subsequent .Update — DIMM temps) already
            # have valid values when the SensorThread makes its real first
            # poll.  6 cycles is required because the bridge strides Storage
            # hardware updates 1-in-3 (Program.cs: pollCount % 3 == 0), so
            # 6 polls = 2 Storage updates, which lets the NVMe driver's async
            # SMART read land on the second update.
            for _ in range(6):
                get_sensors()
        except Exception as e:
            logger.debug("lhm_bridge prewarm failed: %s", e)
    threading.Thread(target=_worker, daemon=True, name="LhmPrewarm").start()


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
        # ABOVE_NORMAL_PRIORITY_CLASS: keep the bridge schedulable when user
        # workloads (stress tests, games, encoders) saturate every core.
        # Without it, Windows starves the .NET poll process, readline() times
        # out at 15 s, and the sensor bar flips to "---" exactly when the
        # reading matters most.  The CPU cost is negligible — bridge runs for
        # <10 ms every 3 s.
        _proc = subprocess.Popen(
            [exe],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=(subprocess.CREATE_NO_WINDOW
                           | subprocess.ABOVE_NORMAL_PRIORITY_CLASS),
            env=env,
        )
        logger.info("lhm_bridge daemon started (pid %d, above-normal priority)", _proc.pid)
        # Give the .NET CLR + LibreHardwareMonitor computer.Open() time to
        # finish hardware enumeration before the first poll request.  3 s
        # reliably covers systems with multiple NVMe controllers + DIMM SPD;
        # 2 s sometimes left the second NVMe drive un-enumerated on the first
        # poll.  This sleep is fully absorbed by prewarm() running in parallel
        # with hardware fingerprint and tweak application during startup.
        time.sleep(3.0)
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
    if _fail_count <= _BACKOFF_GRACE:
        # Transient miss — let the next call retry immediately.
        _restart_delay_secs = 0.0
    elif _restart_delay_secs < _BACKOFF_MIN:
        _restart_delay_secs = _BACKOFF_MIN
    else:
        _restart_delay_secs = min(_BACKOFF_MAX, _restart_delay_secs * 2)


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
    """Return path to bridge exe if it exists, else empty string.

    The config-supplied `lhm.bridge_exe` override is honored only when the
    ALIENCORE_DEV_BRIDGE_OVERRIDE env var is set to "1" — otherwise an
    attacker who can edit config.json could point AlienCore (running as
    admin) at an arbitrary binary.  Production installs always use the
    bundled lhm_bridge.exe; developers building a custom bridge set the
    env var themselves.
    """
    if os.environ.get("ALIENCORE_DEV_BRIDGE_OVERRIDE") == "1":
        from core import config_manager as cfg
        c = cfg.get()
        override = c.get("lhm", {}).get("bridge_exe", "")
        if override:
            return override if os.path.exists(override) else ""
    return _BRIDGE_EXE if os.path.exists(_BRIDGE_EXE) else ""
