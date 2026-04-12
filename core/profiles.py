"""
AlienCore - profiles.py
Detects what the system is doing and decides which profile to apply.
Detection uses both process names AND load signals together.

Profiles:
  idle       — nothing demanding running, CPU cool
  gaming     — game process detected OR high GPU load
  streaming  — OBS/XSplit detected
  manual     — user locked a profile via tray icon
"""

import logging
import psutil
from core import config_manager as cfg
from core.constants import STREAMING_PROCESSES, GAMING_PROCESSES

logger = logging.getLogger("aliencore.profiles")

# Module-level state
_current_profile  = "idle"
_manual_override  = None   # None = auto, string = locked profile name


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_current() -> str:
    return _manual_override if _manual_override else _current_profile


def is_manual_override() -> bool:
    return _manual_override is not None


def set_manual_override(profile_name: str | None):
    """
    Lock to a specific profile (tray override).
    Pass None to return to automatic detection.
    """
    global _manual_override
    _manual_override = profile_name
    if profile_name:
        logger.info("Manual override set: %s", profile_name)
    else:
        logger.info("Manual override cleared — returning to auto detection")


def evaluate(sensor_readings: dict) -> str:
    """
    Evaluate current system state and return the appropriate profile name.
    Returns the same profile if nothing changed (caller checks for change).
    """
    global _current_profile

    if _manual_override:
        return _manual_override

    c = cfg.get()
    if not c["profiles"]["enabled"]:
        return "idle"

    by_process = c["profiles"]["detect_by_process"]
    by_load    = c["profiles"]["detect_by_load"]

    running = _get_running_processes() if by_process else set()

    # ── Check streaming first (highest priority if both OBS and a game run) ──
    if by_process and _is_streaming(running, c):
        new = "streaming"
    elif by_process:
        # User custom profiles checked before built-in gaming detection
        custom = _check_user_profiles(running, c) if by_process else None
        if custom:
            new = custom
        elif _is_gaming_by_process(running, c):
            new = "gaming"
        elif by_load and _is_gaming_by_load(sensor_readings, c):
            new = "gaming"
        else:
            new = "idle"
    elif by_load and _is_gaming_by_load(sensor_readings, c):
        new = "gaming"
    else:
        new = "idle"

    if new != _current_profile:
        logger.info("Profile change: %s → %s", _current_profile, new)
        _current_profile = new

    return _current_profile


# ─────────────────────────────────────────────────────────────────────────────
# Detection helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_running_processes() -> set:
    """Return a lowercase set of all running process names."""
    try:
        return {p.name().lower() for p in psutil.process_iter(["name"])}
    except Exception as e:
        logger.debug("Process list error: %s", e)
        return set()


def _is_streaming(running: set, c: dict) -> bool:
    known    = {p.lower() for p in STREAMING_PROCESSES}
    custom   = {p.lower() for p in c["profiles"].get("custom_streaming_processes", [])}
    combined = known | custom
    return bool(running & combined)


def _is_gaming_by_process(running: set, c: dict) -> bool:
    known  = {p.lower() for p in GAMING_PROCESSES}
    custom = {p.lower() for p in c["profiles"].get("custom_gaming_processes", [])}
    combined = known | custom
    return bool(running & combined)


def _check_user_profiles(running: set, c: dict) -> str | None:
    """
    Check user-defined profiles in priority order.
    Returns the matching profile name (slug) or None.
    """
    user_profiles = c["profiles"].get("user_profiles", [])
    if not user_profiles:
        return None
    sorted_profiles = sorted(user_profiles, key=lambda p: p.get("priority", 50))
    for up in sorted_profiles:
        processes = {p.lower() for p in up.get("processes", [])}
        if processes and running & processes:
            return up["name"]
    return None


def get_user_profile_info(name: str) -> dict | None:
    """Return the user profile dict for the given name slug, or None."""
    c = cfg.get()
    for up in c["profiles"].get("user_profiles", []):
        if up.get("name") == name:
            return up
    return None


def _is_gaming_by_load(sensors: dict, c: dict) -> bool:
    gpu_thresh = c["profiles"].get("gaming_gpu_threshold", 40)
    cpu_thresh = c["profiles"].get("gaming_cpu_threshold", 30)

    gpu_load = sensors.get("gpu_load")
    cpu_load = sensors.get("cpu_load_pct")

    if gpu_load is not None and gpu_load >= gpu_thresh:
        return True
    if cpu_load is not None and gpu_load is not None:
        # Both CPU and GPU elevated = likely gaming even if GPU% is moderate
        if cpu_load >= cpu_thresh and gpu_load >= (gpu_thresh * 0.6):
            return True
    return False
