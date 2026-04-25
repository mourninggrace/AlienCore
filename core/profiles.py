"""
AlienCore - profiles.py
Detects what the system is doing and decides which profile to apply.
Detection uses both process names AND load signals together.

Profiles:
  idle       — nothing demanding running, CPU cool
  working    — productivity load: many tabs, many apps, sustained CPU without GPU heat
  gaming     — game process detected OR high GPU load
  streaming  — OBS/XSplit detected
  manual     — user locked a profile via tray icon

Priority on conflicts: streaming > gaming > working > idle.
"""

import logging
import time
import psutil
from core import config_manager as cfg
from core.constants import STREAMING_PROCESSES, GAMING_PROCESSES, WORKING_PROCESSES

logger = logging.getLogger("aliencore.profiles")

# Module-level state
_current_profile  = "idle"
_manual_override  = None   # None = auto, string = locked profile name

# Load-based detection hysteresis — require a condition to hold for N consecutive
# evaluations before committing to a profile switch.  Prevents false triggers from
# momentary GPU/CPU spikes (browser rendering, antivirus, Windows updates, etc.).
_load_hit_count   = 0        # consecutive evaluations where load looked like gaming
_LOAD_HIT_NEEDED  = 3        # must be true this many times in a row (~30 s at 10 s/eval)

# Working-profile hysteresis — productivity load is noisier than gaming
# (background indexing, AV scans, single-tab JS GC), so we require a longer
# hold window before committing.  ~50-60 s at the default 10 s/eval cadence.
_work_hit_count   = 0
_WORK_HIT_NEEDED  = 5

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
      - Load-based gaming: requires _LOAD_HIT_NEEDED consecutive evaluations
        before committing, preventing false triggers from momentary spikes.
      - Load-based working: requires _WORK_HIT_NEEDED consecutive evaluations
        (longer hold — productivity signals are noisier than gaming).
      - Descending: counters tick down before falling all the way to idle, so
        a brief lull in CPU/GPU doesn't drop the profile mid-session.
    """
    global _current_profile, _load_hit_count, _work_hit_count

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
        _work_hit_count = 0
        new = "streaming"
    elif user_match:
        _load_hit_count = 0
        _work_hit_count = 0
        new = user_match
    elif by_process and _is_gaming_by_process(running, c):
        _load_hit_count = 0
        _work_hit_count = 0
        new = "gaming"
    else:
        # ── Load-based detection — gaming first (highest load priority) ──
        has_work_proc = by_process and _is_working_by_process(running, c)
        wants_gaming  = by_load and _is_gaming_by_load(sensor_readings, c)
        wants_working = by_load and _is_working_by_load(
            sensor_readings, c, has_work_proc)

        if wants_gaming:
            if _load_hit_count < _LOAD_HIT_NEEDED:
                _load_hit_count += 1
            if _load_hit_count >= _LOAD_HIT_NEEDED:
                # Already past the working bar — pre-fill so descending into
                # working from gaming doesn't have to count up from zero.
                _work_hit_count = _WORK_HIT_NEEDED
                new = "gaming"
            else:
                logger.debug("Load gaming signal: hit %d/%d (holding %s)",
                             _load_hit_count, _LOAD_HIT_NEEDED, _current_profile)
                new = _current_profile
        elif wants_working:
            # Tick gaming counter down (signal dropped from gaming-level load)
            if _current_profile == "gaming" and _load_hit_count > 0:
                _load_hit_count -= 1
                logger.debug("Gaming signal gone but working active: load count → %d",
                             _load_hit_count)
                new = _current_profile
            else:
                if _work_hit_count < _WORK_HIT_NEEDED:
                    _work_hit_count += 1
                if _work_hit_count >= _WORK_HIT_NEEDED:
                    _load_hit_count = 0
                    new = "working"
                else:
                    logger.debug("Working signal: hit %d/%d (holding %s)",
                                 _work_hit_count, _WORK_HIT_NEEDED, _current_profile)
                    new = _current_profile
        else:
            # Quiet — descend through the counters before settling on idle
            if _current_profile == "gaming" and _load_hit_count > 0:
                _load_hit_count -= 1
                logger.debug("Quiet — gaming load count → %d (holding gaming)",
                             _load_hit_count)
                new = _current_profile
            elif _current_profile == "working" and _work_hit_count > 0:
                _work_hit_count -= 1
                logger.debug("Quiet — working count → %d (holding working)",
                             _work_hit_count)
                new = _current_profile
            else:
                _load_hit_count = 0
                _work_hit_count = 0
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
        processes = {str(p).lower() for p in up.get("processes", [])}
        if processes and running & processes:
            return up.get("name") or None
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


def _is_working_by_process(running: set, c: dict) -> bool:
    """True if any productivity-class process is running (used as load bias)."""
    from core.constants import WORKING_PROCESSES
    known    = {p.lower() for p in WORKING_PROCESSES}
    custom   = {p.lower() for p in c["profiles"].get("custom_working_processes", [])}
    return bool(running & (known | custom))


def _working_threshold(c: dict) -> float:
    """
    CPU% threshold for working detection, scaled by hardware tier.
    Weaker hardware promotes to working sooner; powerful hardware stays idle longer.
      tier 0 (low,  <= 8 GB)  → 0.70x base
      tier 1 (norm, <=16 GB)  → 0.85x base
      tier 2 (high, <=32 GB)  → 1.00x base
      tier 3 (xl,   > 32 GB)  → 1.15x base
    """
    from core import mem_tier
    base = c["profiles"].get("working_cpu_threshold", 25)
    tier_mult = (0.70, 0.85, 1.00, 1.15)[mem_tier.tier_idx()]
    return base * tier_mult


def _is_working_by_load(sensors: dict, c: dict, has_working_proc: bool) -> bool:
    """
    True when CPU shows productivity-class load *without* gaming-class GPU heat.
    A productivity-class process running biases the threshold down by 5 pp,
    so e.g. Chrome + Slack + IDE promote to working sooner than the same CPU
    load with no productivity processes detected.
    """
    cpu_load = sensors.get("cpu_load_pct")
    if cpu_load is None:
        return False
    gpu_load = sensors.get("gpu_load")

    threshold = _working_threshold(c)
    if has_working_proc:
        threshold = max(8.0, threshold - 5.0)

    if cpu_load < threshold:
        return False
    # Don't claim "working" if GPU is hot — that's the gaming detector's territory.
    gpu_thresh = c["profiles"].get("gaming_gpu_threshold", 40)
    if gpu_load is not None and gpu_load >= gpu_thresh:
        return False
    return True
