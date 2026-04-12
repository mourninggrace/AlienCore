"""
AlienCore - lhm_manager.py
Runs the lhm_bridge .NET executable to read hardware sensors.

The bridge (tools/lhm_bridge/dist/lhm_bridge.exe) uses LibreHardwareMonitorLib
via NuGet to read all hardware sensors and writes a JSON array to stdout.
AlienCore calls it as a short-lived subprocess — no LHM application needed.
"""

import json
import logging
import os
import subprocess
import time

logger = logging.getLogger("aliencore.lhm")

_BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BRIDGE_EXE = os.path.join(_BASE_DIR, "tools", "lhm_bridge", "dist", "lhm_bridge.exe")

# Failure tracking — used to control log verbosity and retry behaviour
_fail_count     = 0   # consecutive failures since last success
_ever_succeeded = False


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_sensors() -> list:
    """
    Run the bridge exe and return a flat list of sensor readings.
    Each entry: {name, hardware, hardwareType, type, value}
    where `type` is the LHM SensorType string (Temperature, Power, Load, etc.)
    Returns empty list on any error.

    Retry behaviour: on the first failure (or while the bridge has never
    succeeded yet — typical immediately after Windows boot), one automatic
    retry is made after a short pause so transient startup errors self-heal
    without requiring an AlienCore restart.
    """
    global _fail_count, _ever_succeeded

    exe = _bridge_path()
    if not exe:
        _log_failure("lhm_bridge.exe not found at expected path")
        return []

    result = _run_bridge(exe)
    if result is not None:
        _fail_count     = 0
        _ever_succeeded = True
        return result

    # First attempt failed — retry once if bridge has never succeeded (boot
    # transient) or this is the very first failure in a previously-good run.
    if not _ever_succeeded or _fail_count == 0:
        time.sleep(2)
        result = _run_bridge(exe)
        if result is not None:
            _fail_count     = 0
            _ever_succeeded = True
            return result

    _fail_count += 1
    return []


def stop():
    """No-op — bridge is a short-lived subprocess, nothing to stop."""
    pass


def bridge_exe_path() -> str:
    """Return the resolved bridge exe path (may not exist yet)."""
    return _bridge_path() or _BRIDGE_EXE


# ─────────────────────────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────────────────────────

def _run_bridge(exe: str):
    """
    Execute the bridge and return parsed sensor list, or None on any error.
    Logs failures at WARNING for the first occurrence and every 10th thereafter;
    subsequent failures are logged at DEBUG to avoid log spam.
    """
    try:
        proc = subprocess.run(
            [exe],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if proc.returncode != 0:
            _log_failure(f"bridge exited {proc.returncode}: {proc.stderr.strip() or '(no stderr)'}")
            return None
        sensors = json.loads(proc.stdout)
        return sensors
    except subprocess.TimeoutExpired:
        _log_failure("bridge timed out after 10 seconds (hardware may not be ready yet)")
        return None
    except json.JSONDecodeError as e:
        _log_failure(f"bridge output was not valid JSON: {e}")
        return None
    except FileNotFoundError:
        _log_failure(f"bridge exe missing: {exe}")
        return None
    except Exception as e:
        _log_failure(f"bridge call failed: {e}")
        return None


def _log_failure(msg: str):
    """Log bridge failures at WARNING for the first hit and every 10th; DEBUG otherwise."""
    if _fail_count == 0 or _fail_count % 10 == 0:
        logger.warning("lhm_bridge: %s (consecutive failures: %d)", msg, _fail_count + 1)
    else:
        logger.debug("lhm_bridge: %s", msg)


def _bridge_path() -> str:
    """Return path to bridge exe if it exists, else empty string."""
    from core import config_manager as cfg
    c = cfg.get()
    override = c.get("lhm", {}).get("bridge_exe", "")
    path = override if override else _BRIDGE_EXE
    return path if os.path.exists(path) else ""
