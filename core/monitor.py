"""
AlienCore - monitor.py
The resident loop. Runs on a background thread forever.
  - Polls sensors
  - Evaluates which profile fits
  - Applies profile tweaks on change
  - Dynamically adjusts CPU ceiling at idle based on temp + load
  - Clears standby RAM cache when idle and pressure is high
  - Reloads config when flagged (after settings save)
"""

import logging
import subprocess
import threading
import time
import psutil
from core import config_manager as cfg
from core import profiles, sensors, tweaks, learning

logger = logging.getLogger("aliencore.monitor")

_thread  = None
_running = False
_hardware = {}
_reload_flag = threading.Event()

# Track last profile so we only apply tweaks on actual changes
_last_profile = None


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def start(hardware: dict):
    global _thread, _running, _hardware
    _hardware = hardware
    _running  = True
    _thread   = threading.Thread(target=_loop, name="MonitorThread", daemon=True)
    _thread.start()
    logger.info("Monitor loop started.")


def stop():
    global _running
    _running = False
    logger.info("Monitor loop stopped.")


def request_config_reload():
    """Signal the monitor loop to reload config on next iteration."""
    _reload_flag.set()


# ─────────────────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────────────────

def _loop():
    global _last_profile

    from core.constants import (
        PROFILE_EVAL_INTERVAL, SENSOR_POLL_INTERVAL
    )

    iteration   = 0
    standby_counter = 0

    while _running:
        try:
            # ── Config reload if settings were saved ──
            if _reload_flag.is_set():
                cfg.reload()
                _reload_flag.clear()
                logger.info("Config reloaded in monitor loop.")

            c = cfg.get()
            readings = sensors.get_readings()

            # ── Profile evaluation (every PROFILE_EVAL_INTERVAL seconds) ──
            if iteration % max(1, PROFILE_EVAL_INTERVAL // SENSOR_POLL_INTERVAL) == 0:
                profile = profiles.evaluate(readings)

                if profile != _last_profile:
                    tweaks.apply_profile(profile, _hardware)
                    # Log profile switch for learning
                    trigger = "manual" if profiles.is_manual_override() else "process"
                    learning.log_profile_switch(profile, trigger, readings)
                    _last_profile = profile

                    # Toast notification if enabled
                    if c["service"].get("notify_on_profile_switch"):
                        _toast(f"AlienCore: switched to {profile} profile")

            # ── Thermal event logging ──
            cpu_temp = readings.get("cpu_temp_avg")
            if cpu_temp is not None:
                thresh = c.get("thresholds", {})
                if cpu_temp >= thresh.get("cpu_crit", 95):
                    learning.log_thermal_event("crit", cpu_temp,
                                               _last_profile or "idle",
                                               _current_cpu_ceiling or 0)
                elif cpu_temp >= thresh.get("cpu_warn", 80):
                    learning.log_thermal_event("warn", cpu_temp,
                                               _last_profile or "idle",
                                               _current_cpu_ceiling or 0)

            # ── Dynamic CPU throttle adjustment at idle ──
            if (_last_profile == "idle"
                    and c["cpu"]["enabled"]
                    and c["cpu"]["dynamic_throttle"]):
                _adjust_cpu_ceiling_dynamically(readings, c)

            # ── Standby cache clearing ──
            if c["ram"]["enabled"] and c["ram"]["clear_standby_cache_on_idle"]:
                standby_counter += 1
                # Check every ~5 minutes at idle
                if _last_profile == "idle" and standby_counter >= 150:
                    standby_counter = 0
                    _maybe_clear_standby(readings)

            # ── DIMM thermal throttle protection ──
            if c["ram"].get("dimm_throttle_protection", False):
                _check_dimm_temps(readings, c)

            # ── Memory leak watchdog ──
            if c["ram"].get("leak_watchdog_enabled", False):
                _run_leak_watchdog(c)

            # ── TVB optimizer ──
            if (_last_profile == "idle"
                    and c["cpu"].get("tvb_optimizer", False)):
                _run_tvb_optimizer(readings, c)

            iteration += 1

        except Exception as e:
            logger.error("Monitor loop error: %s", e)

        time.sleep(SENSOR_POLL_INTERVAL)


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic CPU ceiling
# ─────────────────────────────────────────────────────────────────────────────

_current_cpu_ceiling  = None
_pending_cpu_ceiling  = None   # target we're waiting to confirm
_pending_since        = None   # time we first saw this target
_HYSTERESIS_SECS      = 10     # must hold target for this long before applying


def _adjust_cpu_ceiling_dynamically(readings: dict, c: dict):
    global _current_cpu_ceiling, _pending_cpu_ceiling, _pending_since

    import time
    cpu_temp  = readings.get("cpu_temp_avg")
    cpu_load  = readings.get("cpu_load_pct")
    temp_trig = c["cpu"]["throttle_temp_trigger"]
    min_ceil  = c["cpu"]["idle_max_state_pct"]

    if cpu_temp is None or cpu_load is None:
        return

    # ── Calculate target ceiling ──
    if cpu_temp >= temp_trig + 15:
        # Very hot — clamp hard immediately, no hysteresis
        target = min_ceil
    elif cpu_temp >= temp_trig:
        # Warm — interpolate between min_ceil and 80%
        ratio  = (cpu_temp - temp_trig) / 15.0
        target = int(min_ceil + (1 - ratio) * (80 - min_ceil))
        target = max(min_ceil, min(80, target))
    elif cpu_load < 8:
        # Cool and idle — use floor
        target = min_ceil
    elif cpu_load < 20:
        # Cool and light — moderate ceiling
        target = min(60, min_ceil + 20)
    else:
        # Cool and busy — full headroom
        target = 100

    # Snap to nearest 5%
    target = round(target / 5) * 5
    target = max(min_ceil, min(100, target))

    # ── Emergency: apply immediately if very hot ──
    if cpu_temp >= temp_trig + 15 and target != _current_cpu_ceiling:
        logger.debug("CPU ceiling EMERGENCY: %d%% → %d%%  (temp=%.1f°C)",
                     _current_cpu_ceiling or 0, target, cpu_temp)
        tweaks._set_max_processor_state(target, "AC", dry_run=False)
        tweaks._set_max_processor_state(target, "DC", dry_run=False)
        _current_cpu_ceiling = target
        _pending_cpu_ceiling = None
        _pending_since = None
        return

    # ── Hysteresis: only apply after target is stable for N seconds ──
    now = time.time()
    if target != _current_cpu_ceiling:
        if target != _pending_cpu_ceiling:
            # New candidate — start timer
            _pending_cpu_ceiling = target
            _pending_since = now
        elif (now - _pending_since) >= _HYSTERESIS_SECS:
            # Stable long enough — apply it
            logger.debug("CPU ceiling: %d%% → %d%%  (temp=%.1f°C load=%.1f%%)",
                         _current_cpu_ceiling or 0, target, cpu_temp, cpu_load)
            tweaks._set_max_processor_state(target, "AC", dry_run=False)
            tweaks._set_max_processor_state(target, "DC", dry_run=False)
            try:
                learning.log_cpu_ceiling(_current_cpu_ceiling or 0, target,
                                         cpu_temp, cpu_load)
            except Exception:
                pass
            _current_cpu_ceiling = target
            _pending_cpu_ceiling = None
            _pending_since = None
    else:
        # Already at target — reset pending
        _pending_cpu_ceiling = None
        _pending_since = None


# ─────────────────────────────────────────────────────────────────────────────
# Standby cache
# ─────────────────────────────────────────────────────────────────────────────

def _maybe_clear_standby(readings: dict):
    """Clear standby RAM cache if memory pressure is significant."""
    ram_pct = readings.get("ram_usage_pct")
    if ram_pct is None:
        return
    if ram_pct < 70:
        logger.debug("Standby clear skipped (RAM at %.0f%% — not needed)", ram_pct)
        return
    try:
        # EmptyStandbyList.exe is a common utility for this
        # Falls back to a PowerShell method if not present
        import os
        esl = os.path.join(os.environ.get("WINDIR","C:\\Windows"),
                           "System32", "EmptyStandbyList.exe")
        if os.path.exists(esl):
            subprocess.run([esl, "standbylist"], capture_output=True, timeout=10)
            logger.info("Standby cache cleared (RAM was at %.0f%%)", ram_pct)
        else:
            # EmptyStandbyList.exe is required to clear the standby list.
            # There is no reliable PowerShell equivalent — skip and log.
            logger.warning(
                "Standby cache clear skipped: EmptyStandbyList.exe not found at %s. "
                "RAM is at %.0f%%. Place EmptyStandbyList.exe in System32 to enable "
                "this feature.", esl, ram_pct
            )
    except Exception as e:
        logger.warning("Standby cache clear failed: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Toast notification
# ─────────────────────────────────────────────────────────────────────────────

def _check_dimm_temps(readings: dict, c: dict):
    """Alert + reduce CPU ceiling when DIMM temps exceed threshold."""
    threshold = c["ram"].get("dimm_throttle_temp_c", 52)
    ram_temps = readings.get("ram_temps", [])
    hot_dimms = [d for d in ram_temps if d.get("temp_c", 0) >= threshold]
    if hot_dimms:
        hottest = max(d["temp_c"] for d in hot_dimms)
        logger.warning("DIMM thermal alert: %.0f°C (threshold %.0f°C) — reducing CPU ceiling",
                       hottest, threshold)
        # Reduce CPU ceiling to relieve memory controller heat
        tweaks._set_max_processor_state(60, "AC", dry_run=False)
        tweaks._set_max_processor_state(60, "DC", dry_run=False)


def _run_leak_watchdog(c: dict):
    """Update the memory leak watchdog with current config parameters."""
    try:
        from core import ram_watchdog
        window  = c["ram"].get("leak_window_minutes", 5.0)
        thresh  = c["ram"].get("leak_threshold_mb_per_min", 50.0)
        ram_watchdog.update(window_minutes=window, threshold_mb_per_min=thresh)
    except Exception as e:
        logger.debug("Leak watchdog error: %s", e)


def _run_tvb_optimizer(readings: dict, c: dict):
    """
    TVB optimizer: if CPU temp is within 5°C of the TVB threshold and we're
    at idle, nudge the idle ceiling down by 5% to keep temps below TVB.
    This preserves single-threaded boost by keeping the CPU cooler.
    """
    global _current_cpu_ceiling
    from core.constants import TVB_TEMP_THRESHOLD
    cpu_temp = readings.get("cpu_temp_avg")
    if cpu_temp is None:
        return
    margin = TVB_TEMP_THRESHOLD - cpu_temp
    if 0 <= margin <= 5:
        # Getting close — pull ceiling down slightly
        current_ceil = _current_cpu_ceiling or c["cpu"]["idle_max_state_pct"]
        new_ceil     = max(c["cpu"]["idle_max_state_pct"], current_ceil - 5)
        if new_ceil != _current_cpu_ceiling:
            logger.debug("TVB optimizer: nudging CPU ceiling to %d%% (temp=%.1f°C, TVB=%.0f°C)",
                         new_ceil, cpu_temp, TVB_TEMP_THRESHOLD)
            tweaks._set_max_processor_state(new_ceil, "AC", dry_run=False)
            tweaks._set_max_processor_state(new_ceil, "DC", dry_run=False)
            _current_cpu_ceiling = new_ceil


def _toast(message: str):
    try:
        script = (
            f"[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
            f"ContentType = WindowsRuntime] | Out-Null; "
            f"$template = [Windows.UI.Notifications.ToastNotificationManager]"
            f"::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText01); "
            f"$template.SelectSingleNode('//text[@id=1]').InnerText = '{message}'; "
            f"$toast = [Windows.UI.Notifications.ToastNotification]::new($template); "
            f"[Windows.UI.Notifications.ToastNotificationManager]"
            f"::CreateToastNotifier('AlienCore').Show($toast)"
        )
        subprocess.run(["powershell", "-Command", script],
                       capture_output=True, timeout=5)
    except Exception:
        pass   # Toast is optional — never crash over it
