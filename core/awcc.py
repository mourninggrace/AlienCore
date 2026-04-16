"""
AlienCore - awcc.py
Deep integration with Alienware Command Center via WMI.

Interface: AWCCWmiMethodFunction in namespace root\\WMI (reverse-engineered,
confirmed working on m18 R2 by tcc-g15 / alienfx-tools communities).

All methods pack arguments as a single uint32:
  Byte 0: operation code
  Byte 1: arg1 (fan ID, sensor ID, or profile ID)
  Byte 2: arg2
  Byte 3: arg3
Return 0xFFFFFFFF / 0xFFFFFFFE = failure.

m18 R2 uses USTT profile IDs (0xA0–0xA5). Legacy IDs (0x96–0x99) do NOT work.
Fan boost control requires setting Custom profile (0x00) first.
Fan IDs are discovered at runtime (typically 0x31–0x63 range on m18).

Requirements:
  - AWCC service (AWCCService.exe) must be running
  - Run as Administrator
  - pip install WMI pywin32
"""

import logging
import queue as _queue
import threading
import time
from typing import Optional

logger = logging.getLogger("aliencore.awcc")

# ── USTT Thermal profile IDs (m18 R2 / post-2019 Alienware) ──────────────────
PROFILE_CUSTOM        = 0x00   # manual fan control mode (required before fan boost)
PROFILE_BALANCED      = 0xA0   # general desktop use
PROFILE_BALANCED_HP   = 0xA1   # gaming + background workloads
PROFILE_COOL          = 0xA2   # aggressive cooling, moderate performance
PROFILE_QUIET         = 0xA3   # minimal fan noise
PROFILE_PERFORMANCE   = 0xA4   # maximum performance
PROFILE_LOW_POWER     = 0xA5   # battery conservation
PROFILE_GMODE         = 0xAB   # G-Mode (maximum performance + max fans, loud)

PROFILE_NAMES = {
    PROFILE_CUSTOM:      "Custom",
    PROFILE_BALANCED:    "Balanced",
    PROFILE_BALANCED_HP: "Balanced Performance",
    PROFILE_COOL:        "Cool",
    PROFILE_QUIET:       "Quiet",
    PROFILE_PERFORMANCE: "Performance",
    PROFILE_LOW_POWER:   "Low Power",
    PROFILE_GMODE:       "G-Mode",
}

# AlienCore profile → AWCC USTT profile mapping
ALIENCORE_TO_AWCC = {
    "idle":      PROFILE_BALANCED,
    "gaming":    PROFILE_PERFORMANCE,
    "streaming": PROFILE_BALANCED_HP,
    "manual":    None,   # don't change AWCC profile on manual override
}

# Failure sentinel values from the WMI interface
_WMI_FAIL = (0xFFFFFFFF, 0xFFFFFFFE)

# ── Module state ──────────────────────────────────────────────────────────────
_lock         = threading.Lock()
_awcc         = None          # AWCCWmiMethodFunction WMI instance
_available    = None          # True/False/None (None = not yet tested)
_fans         = []            # discovered [(fan_id, [sensor_ids])]
_fans_ready   = False
_prev_profile = None          # profile saved before Turbo Cool activates

# ── Owning thread ────────────────────────────────────────────────────────────
# COM STA objects may only be used on the thread that created them.
# We track which thread owns the current _awcc connection.  If a different
# thread tries to use it, we reset and reconnect on that thread instead.
_owning_thread_id: int | None = None

# ── WMI call-failure backoff ──────────────────────────────────────────────────
# When AWCC WMI calls repeatedly fail (provider unhealthy), backing off prevents
# WmiPrvSE.exe from accumulating error-recovery work at 2-second poll intervals.
_fail_count      = 0
_backoff_until   = 0.0     # epoch time — skip calls until this timestamp
_FAIL_THRESHOLD  = 5       # consecutive failures before backing off
_BACKOFF_SECS    = 120     # back off for 2 minutes, then retry once

# ── Write command queue ───────────────────────────────────────────────────────
# COM STA objects belong to the thread that created them (the SensorThread).
# Callers on other threads (tray, profiles, turbo_cool) MUST NOT call AWCC
# WMI methods directly — they post callables here instead.
# The SensorThread drains this queue each poll cycle via drain_queue().
_cmd_queue: _queue.Queue = _queue.Queue()


def _record_awcc_failure():
    """Track consecutive WMI failures; engage backoff when threshold is reached."""
    global _fail_count, _backoff_until, _awcc, _available, _fans, _fans_ready
    _fail_count += 1
    if _fail_count >= _FAIL_THRESHOLD:
        _backoff_until = time.time() + _BACKOFF_SECS
        logger.warning(
            "AWCC WMI: %d consecutive failures — backing off for %ds, will reconnect after",
            _fail_count, _BACKOFF_SECS
        )
        _fail_count  = 0
        # Drop the stale WMI object so _try_connect() is called fresh after backoff.
        _awcc        = None
        _available   = None
        _fans        = []
        _fans_ready  = False


def _record_awcc_success():
    global _fail_count
    _fail_count = 0


def _awcc_backed_off() -> bool:
    """Return True if we are currently in a backoff window."""
    return time.time() < _backoff_until


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def is_available() -> bool:
    """Return True if AWCC WMI interface is accessible and not in backoff.

    Routes through _instance() so the connection is established (or rebuilt)
    on the calling thread.  COM STA objects belong to the thread that created
    them — bypassing this check causes CO_E_NOTINITIALIZED on cross-thread reuse.
    """
    if _awcc_backed_off():
        return False
    return _instance() is not None


def get_profile_name(profile_id: int) -> str:
    return PROFILE_NAMES.get(profile_id, f"Unknown (0x{profile_id:02X})")


def get_active_profile() -> Optional[int]:
    """Return the currently active AWCC thermal profile ID, or None on failure."""
    inst = _instance()
    if not inst:
        return None
    try:
        val = inst.Thermal_Information(0x0B)[0]
        if val in _WMI_FAIL:
            _record_awcc_failure()
            return None
        _record_awcc_success()
        return val
    except Exception as e:
        logger.debug("AWCC get_active_profile error: %s", e)
        _record_awcc_failure()
        return None


def set_profile(profile_id: int) -> bool:
    """
    Activate a USTT thermal profile.
    Returns True on success.
    """
    inst = _instance()
    if not inst:
        return False
    try:
        arg    = (profile_id << 8) | 0x01
        result = inst.Thermal_Control(arg)[0]
        if result == 0:
            logger.info("AWCC profile set: %s", get_profile_name(profile_id))
            return True
        logger.warning("AWCC set_profile failed (result=0x%X)", result)
        return False
    except Exception as e:
        logger.warning("AWCC set_profile error: %s", e)
        return False


def apply_aliencore_profile(profile_name: str) -> bool:
    """
    Map an AlienCore profile name (idle/gaming/streaming) to an AWCC profile
    and apply it. Returns True on success.
    """
    awcc_id = ALIENCORE_TO_AWCC.get(profile_name)
    if awcc_id is None:
        return False   # manual override — don't touch AWCC
    return set_profile(awcc_id)


def get_fan_rpms() -> list:
    """
    Return list of {fan_id, rpm} for all discovered fans.
    Discovers fans on first call and caches the IDs.
    """
    if not is_available():
        return []
    _ensure_fans_discovered()
    result = []
    for fan_id, _ in _fans:
        rpm = get_fan_rpm(fan_id)
        if rpm is not None:
            result.append({"fan_id": fan_id, "rpm": rpm})
    return result


def get_fan_rpm(fan_id: int) -> Optional[int]:
    inst = _instance()
    if not inst:
        return None
    try:
        val = inst.Thermal_Information((fan_id << 8) | 0x05)[0]
        if val in _WMI_FAIL:
            _record_awcc_failure()
            return None
        _record_awcc_success()
        return val
    except Exception as e:
        logger.debug("AWCC get_fan_rpm error (fan_id=0x%X): %s", fan_id, e)
        _record_awcc_failure()
        return None


def get_fan_pct(fan_id: int) -> Optional[int]:
    """Fan speed as percent of max."""
    inst = _instance()
    if not inst:
        return None
    try:
        val = inst.Thermal_Information((fan_id << 8) | 0x06)[0]
        return None if val in _WMI_FAIL else val
    except Exception as e:
        logger.debug("AWCC get_fan_pct error: %s", e)
        return None


def get_awcc_temps() -> list:
    """
    Return list of {sensor_id, temp_c} for all AWCC thermal sensors.
    Useful for cross-checking with LHM data.
    """
    if not is_available():
        return []
    _ensure_fans_discovered()
    result = []
    seen   = set()
    for _, sensor_ids in _fans:
        for sid in sensor_ids:
            if sid in seen:
                continue
            seen.add(sid)
            temp = get_sensor_temp(sid)
            if temp is not None and 0 < temp < 120:
                result.append({"sensor_id": sid, "temp_c": temp})
    return result


def get_sensor_temp(sensor_id: int) -> Optional[int]:
    inst = _instance()
    if not inst:
        return None
    try:
        val = inst.Thermal_Information((sensor_id << 8) | 0x04)[0]
        return None if val in _WMI_FAIL else val
    except Exception as e:
        logger.debug("AWCC get_sensor_temp error: %s", e)
        return None


def set_fan_boost(fan_id: int, boost_pct: int) -> bool:
    """
    Set fan addon speed as a percentage (0–100).
    NOTE: Custom profile (0x00) must be active first — call
    set_profile(PROFILE_CUSTOM) before calling this.
    """
    inst = _instance()
    if not inst:
        return False
    try:
        boost  = max(0, min(100, boost_pct))
        arg    = (boost << 16) | (fan_id << 8) | 0x02
        result = inst.Thermal_Control(arg)[0]
        return result == 0
    except Exception as e:
        logger.debug("AWCC set_fan_boost error: %s", e)
        return False


def turbo_cool_activate() -> bool:
    """
    Activate maximum fan speed for Turbo Cool mode.
    Saves the current AWCC profile so it can be restored on deactivate.
    Returns True if fans were successfully boosted.
    """
    global _prev_profile
    if not is_available():
        return False

    _ensure_fans_discovered()
    _prev_profile = get_active_profile()

    # Custom profile required to enable manual fan boost
    if not set_profile(PROFILE_CUSTOM):
        logger.warning("AWCC turbo cool: failed to set Custom profile")
        return False

    # Small delay for profile switch to take effect
    time.sleep(0.3)

    success = True
    for fan_id, _ in _fans:
        if not set_fan_boost(fan_id, 100):
            logger.warning("AWCC turbo cool: fan boost failed (fan_id=0x%X)", fan_id)
            success = False

    if success:
        logger.info("AWCC turbo cool: all %d fan(s) at max boost", len(_fans))
    return success


def turbo_cool_deactivate(restore_profile: Optional[int] = None) -> bool:
    """
    Deactivate Turbo Cool and restore previous AWCC profile.
    Pass restore_profile to override the auto-saved profile.
    """
    global _prev_profile
    if not is_available():
        return False

    target = restore_profile if restore_profile is not None else _prev_profile
    if target is None:
        target = PROFILE_BALANCED   # safe default

    # Clear manual boosts first
    for fan_id, _ in _fans:
        set_fan_boost(fan_id, 0)

    time.sleep(0.1)

    ok = set_profile(target)
    if ok:
        logger.info("AWCC turbo cool: deactivated, restored profile %s",
                    get_profile_name(target))
    _prev_profile = None
    return ok


def get_gmode() -> bool:
    """Return True if G-Mode is currently active."""
    inst = _instance()
    if not inst:
        return False
    try:
        val = inst.GameShiftStatus(0x02)[0]
        return bool(val) and val not in _WMI_FAIL
    except Exception as e:
        logger.debug("AWCC get_gmode error: %s", e)
        return False


def toggle_gmode() -> bool:
    """Toggle G-Mode on/off. Returns new state (True = on)."""
    inst = _instance()
    if not inst:
        return False
    try:
        inst.GameShiftStatus(0x01)
        state = get_gmode()
        logger.info("AWCC G-Mode toggled: %s", "ON" if state else "OFF")
        return state
    except Exception as e:
        logger.warning("AWCC toggle_gmode error: %s", e)
        return False


def set_gmode(enabled: bool) -> bool:
    """Set G-Mode to a specific state. Returns True if already in that state or toggle succeeded."""
    current = get_gmode()
    if current == enabled:
        return True
    toggle_gmode()
    return get_gmode() == enabled


def get_fan_ids() -> list:
    """Return list of discovered fan IDs."""
    _ensure_fans_discovered()
    return [f[0] for f in _fans]


def get_system_info() -> dict:
    """Return summary of AWCC-visible system: fan count, sensor count, profile count."""
    inst = _instance()
    if not inst:
        return {}
    try:
        val = inst.Thermal_Information(0x02)[0]
        if val in _WMI_FAIL:
            return {}
        # Packed: fan_count (byte 0), sensor_count (byte 1), profile_count (byte 2)
        return {
            "fan_count":     val & 0xFF,
            "sensor_count":  (val >> 8) & 0xFF,
            "profile_count": (val >> 16) & 0xFF,
        }
    except Exception as e:
        logger.debug("AWCC get_system_info error: %s", e)
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────────────────────────

def _try_connect() -> bool:
    """Attempt to connect to the AWCC WMI interface. Returns True on success."""
    # CoInitialize is the SensorThread's responsibility (_poll_loop calls it).
    # Calling it again here imbalances the COM apartment reference count and
    # can cause CO_E_NOTINITIALIZED on subsequent WMI calls.
    try:
        import wmi
        wmi_obj = wmi.WMI(namespace=r"root\WMI")
        instances = wmi_obj.AWCCWmiMethodFunction()
        if not instances:
            logger.warning("AWCC WMI: AWCCWmiMethodFunction has no instances")
            return False
        global _awcc, _owning_thread_id
        _awcc = instances[0]
        _owning_thread_id = threading.current_thread().ident
        # Quick health check
        _awcc.Thermal_Information(0x0B)
        logger.info("AWCC WMI interface connected successfully (thread %d)",
                    _owning_thread_id)
        return True
    except AttributeError:
        logger.info("AWCC WMI: AWCCWmiMethodFunction not found "
                    "(AWCC service not running or not installed)")
        return False
    except Exception as e:
        logger.info("AWCC WMI unavailable: %s", e)
        return False


def enqueue(fn) -> None:
    """
    Post a callable to be executed on the SensorThread (AWCC owner).
    Use this from any thread other than SensorThread when you need to make
    a write call (set_profile, set_gmode, fan boost, etc.).
    The callable will run during the next sensor poll cycle.
    """
    _cmd_queue.put(fn)


def drain_queue() -> None:
    """
    Execute all pending AWCC write commands.
    Must be called exclusively from the SensorThread each poll cycle.
    """
    while not _cmd_queue.empty():
        try:
            fn = _cmd_queue.get_nowait()
            fn()
        except Exception as e:
            logger.debug("AWCC queued command error: %s", e)


def _instance():
    """
    Return the AWCC WMI instance.
    Must only be called from the SensorThread (the COM STA owner).
    Calls from other threads return None immediately — they must use enqueue()
    for writes and sensors.get_readings() for reads.
    """
    global _awcc, _available, _owning_thread_id, _fans, _fans_ready
    if _available is False:
        return None

    current_tid = threading.current_thread().ident

    # Wrong-thread call: return None silently.
    # Do NOT reconnect — that causes COM ping-pong and WmiPrvSE.exe CPU spikes.
    if _awcc is not None and _owning_thread_id != current_tid:
        logger.debug("AWCC: _instance() called from non-owner thread %d (owner=%d) — returning None",
                     current_tid, _owning_thread_id)
        return None

    if _awcc is None:
        _available = _try_connect()
    return _awcc if _available else None


def _ensure_fans_discovered():
    """Discover fan IDs the first time, cache forever."""
    global _fans, _fans_ready
    if _fans_ready:
        return
    with _lock:
        if _fans_ready:
            return
        inst = _instance()
        if not inst:
            return
        discovered = []
        # Scan the known fan ID range for m18 R2 (0x31–0x3F covers all real fans).
        # A wider range causes dozens of WMI calls for phantom IDs that GetFanSensors
        # returns non-zero counts for but no real fan data.
        for fan_id in range(0x31, 0x40):
            try:
                arg   = (fan_id << 8) | 0x01
                count = inst.GetFanSensors(arg)[0]
                if count in _WMI_FAIL or count == 0:
                    continue
                sensors_list = []
                for i in range(min(count, 8)):
                    s_arg = (i << 16) | (fan_id << 8) | 0x02
                    sid   = inst.GetFanSensors(s_arg)[0]
                    if sid not in _WMI_FAIL:
                        sensors_list.append(sid)
                discovered.append((fan_id, sensors_list))
                logger.debug("AWCC fan discovered: 0x%X (%d sensor(s))", fan_id, len(sensors_list))
            except Exception:
                continue

        # Validate: keep only fan IDs that actually return a live RPM reading.
        # This prunes phantom IDs that pass the sensor-count check but return
        # WMI_FAIL on every get_fan_rpm call, which pile up fail-count fast.
        validated = []
        for fan_id, sensor_ids in discovered:
            rpm = get_fan_rpm(fan_id)
            if rpm is not None:
                validated.append((fan_id, sensor_ids))
                logger.debug("AWCC fan validated: 0x%X  RPM=%d", fan_id, rpm)
            else:
                logger.debug("AWCC fan pruned (no RPM response): 0x%X", fan_id)

        if validated:
            _fans      = validated
            _fans_ready = True
            logger.info("AWCC: %d fan(s) active: %s",
                        len(_fans),
                        ", ".join(f"0x{fid:X}" for fid, _ in _fans))
        elif discovered:
            # All discovered fans failed validation — keep them anyway as fallback
            _fans      = discovered
            _fans_ready = True
            logger.info("AWCC: %d fan(s) (unvalidated): %s",
                        len(_fans),
                        ", ".join(f"0x{fid:X}" for fid, _ in _fans))
        else:
            # Fallback: probe known common IDs
            for fid in (0x32, 0x33, 0x34, 0x35):
                rpm = get_fan_rpm(fid)
                if rpm is not None:
                    discovered.append((fid, []))
            if discovered:
                _fans      = discovered
                _fans_ready = True
                logger.info("AWCC: %d fan(s) via fallback probe", len(_fans))
            else:
                logger.warning("AWCC: no fans discovered — check AWCC service is running")
                _fans_ready = True
