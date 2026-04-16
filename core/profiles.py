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
import time
import psutil
from core import config_manager as cfg
from core.constants import STREAMING_PROCESSES, GAMING_PROCESSES

logger = logging.getLogger("aliencore.profiles")

# Module-level state
_current_profile  = "idle"
_manual_override  = None   # None = auto, string = locked profile name

# Load-based detection hysteresis — require a condition to hold for N consecutive
# evaluations before committing to a profile switch.  Prevents false triggers from
# momentary GPU/CPU spikes (browser rendering, antivirus, Windows updates, etc.).
_load_hit_count   = 0        # consecutive evaluations where load looked like gaming
_LOAD_HIT_NEEDED  = 3        # must be true this many times in a row (~30 s at 10 s/eval)

# Process-list cache.  psutil.process_iter iterates ~300 PIDs and opens each
# for the Name attribute — measurable CPU.  Cache for a few seconds so that
# evaluate() can check streaming + gaming + user profiles without three
# separate enumerations.
_proc_cache:     set | None = None
_proc_cache_at:  float      = 0.0
_PROC_CACHE_TTL: float      = 5.0


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

    Hysteresis rules:
      - Process-based detection (game/streaming exe found): switches immediately.
      - Load-based detection: requires _LOAD_HIT_NEEDED consecutive evaluations
        before committing to gaming, preventing false triggers from momentary spikes.
      - Switching back to idle from a load-based gaming session is also gated: the
        load must be gone for _LOAD_HIT_NEEDED consecutive evaluations.
    """
    global _current_profile, _load_hit_count

    if _manual_override:
        return _manual_override

    c = cfg.get()
    if not c["profiles"]["enabled"]:
        return "idle"

    by_process = c["profiles"]["detect_by_process"]
    by_load    = c["profiles"]["detect_by_load"]

    running = _get_running_processes() if by_process else set()

    # ── Process-based detection — always immediate, no hysteresis needed ──
    user_match = _check_user_profiles(running, c) if by_process else None
    if by_process and _is_streaming(running, c):
        _load_hit_count = 0
        new = "streaming"
    elif user_match:
        _load_hit_count = 0
        new = user_match
    elif by_process and _is_gaming_by_process(running, c):
        _load_hit_count = 0
        new = "gaming"

    # ── Load-based detection — hysteresis required ──
    elif by_load and _is_gaming_by_load(sensor_readings, c):
        # Cap at _LOAD_HIT_NEEDED so the count-down on signal loss can never
        # exceed the count-up window (otherwise long gaming sessions would
        # require minutes of cool-down before switching back to idle).
        if _load_hit_count < _LOAD_HIT_NEEDED:
            _load_hit_count += 1
        if _load_hit_count >= _LOAD_HIT_NEEDED:
            new = "gaming"
        else:
            logger.debug("Load gaming signal: hit %d/%d (holding %s)",
                         _load_hit_count, _LOAD_HIT_NEEDED, _current_profile)
            new = _current_profile
    else:
        # Load dropped — apply same hysteresis before returning to idle
        if _current_profile == "gaming" and _load_hit_count > 0:
            _load_hit_count -= 1
            logger.debug("Load gaming signal gone: count down to %d (holding gaming)",
                         _load_hit_count)
            new = _current_profile
        else:
            _load_hit_count = 0
            new = "idle"

    if new != _current_profile:
        logger.info("Profile change: %s → %s", _current_profile, new)
        _current_profile = new

    return _current_profile


# ─────────────────────────────────────────────────────────────────────────────
# Detection helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_running_processes() -> set:
    """
    Return a lowercase set of all running process names.
    Cached for _PROC_CACHE_TTL seconds so multiple callers in one eval cycle
    share a single enumeration.
    """
    global _proc_cache, _proc_cache_at
    now = time.time()
    if _proc_cache is not None and (now - _proc_cache_at) < _PROC_CACHE_TTL:
        return _proc_cache
    try:
        # Use .info["name"] — psutil's bulk-attribute fetch is noticeably
        # cheaper than calling p.name() per PID because it avoids repeated
        # handle opens.
        _proc_cache    = {(p.info.get("name") or "").lower()
                          for p in psutil.process_iter(["name"])}
        _proc_cache_at = now
    except Exception as e:
        logger.debug("Process list error: %s", e)
        _proc_cache    = set()
        _proc_cache_at = now
    return _proc_cache


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
    # Both CPU and GPU elevated = likely gaming even if GPU% is moderate.
    # Requires both readings — neither alone is sufficient at the lower bar.
    if (cpu_load is not None and gpu_load is not None
            and cpu_load >= cpu_thresh
            and gpu_load >= (gpu_thresh * 0.6)):
        return True
    return False
