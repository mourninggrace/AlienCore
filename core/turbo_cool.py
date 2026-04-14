"""
AlienCore - turbo_cool.py
Turbo Cool mode — spins fans to maximum for rapid cooling.

Chassis fans are controlled via AWCC WMI (awcc.py).
GPU fan control is not available on laptop RTX GPUs — AWCC manages all chassis fans.

Auto-disables when EITHER:
  - Timer expires (user configured minutes)
  - All temps drop below warning thresholds (user configured)

State is exposed to bar.py and tray.py for visual indicator.
"""

import logging
import threading
import time
from core import config_manager as cfg, sensors
from core.constants import COLOR_COOL, COLOR_WARN, COLOR_HOT

logger = logging.getLogger("aliencore.turbo_cool")

# ── State ─────────────────────────────────────────────────────────────────────
_active       = False
_activated_at = None
_thread       = None
_lock         = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def is_active() -> bool:
    return _active


def activate():
    """Turn Turbo Cool on."""
    global _active, _activated_at, _thread
    with _lock:
        if _active:
            return
        _active       = True
        _activated_at = time.time()
        logger.info("Turbo Cool ACTIVATED")

    # Apply fan changes immediately
    _set_gpu_fan(100)       # no-op on laptop GPU, kept for clarity
    _activate_awcc_fans()

    # Start monitor thread
    _thread = threading.Thread(target=_monitor_loop, daemon=True,
                               name="TurboCoolMonitor")
    _thread.start()


def deactivate(reason: str = "manual"):
    """Turn Turbo Cool off and restore normal fan control."""
    global _active, _activated_at
    with _lock:
        if not _active:
            return
        _active       = False
        _activated_at = None

    logger.info("Turbo Cool DEACTIVATED (%s)", reason)
    _restore_fans()


def toggle():
    """Toggle Turbo Cool on or off."""
    if _active:
        deactivate("manual")
    else:
        activate()


def status_text() -> str:
    """Return short status for bar display."""
    if not _active:
        return None
    elapsed = int(time.time() - (_activated_at or time.time()))
    mins    = elapsed // 60
    secs    = elapsed % 60
    return f"COOL {mins}:{secs:02d}"


# ─────────────────────────────────────────────────────────────────────────────
# Monitor loop — checks timer and temps
# ─────────────────────────────────────────────────────────────────────────────

def _monitor_loop():
    """Runs while Turbo Cool is active — checks auto-disable conditions."""
    while _active:
        time.sleep(5)   # check every 5 seconds
        if not _active:
            break

        c          = cfg.get()
        tc         = c.get("turbo_cool", {})
        max_mins   = tc.get("auto_off_minutes", 10)
        check_temp = tc.get("auto_off_on_cool", True)
        thresh     = c.get("thresholds", {})
        readings   = sensors.get_readings()

        # ── Timer check ──
        elapsed_mins = (time.time() - (_activated_at or time.time())) / 60
        if elapsed_mins >= max_mins:
            deactivate(f"timer ({max_mins} min)")
            return

        # ── Temp threshold check ──
        if check_temp:
            cpu_temp = readings.get("cpu_temp_avg")
            gpu_temp = readings.get("gpu_temp")
            cpu_warn = thresh.get("cpu_warn", 80)
            gpu_warn = thresh.get("gpu_warn", 80)

            cpu_cool = cpu_temp is None or cpu_temp < cpu_warn
            gpu_cool = gpu_temp is None or gpu_temp < gpu_warn

            if cpu_cool and gpu_cool:
                deactivate("temps below warning thresholds")
                return


# ─────────────────────────────────────────────────────────────────────────────
# Fan control
# ─────────────────────────────────────────────────────────────────────────────

def _set_gpu_fan(pct: int):
    """
    GPU fan control via nvidia-smi is not available on this laptop GPU.
    NVIDIA locks fan control on laptop GPUs — AWCC manages chassis fans.
    Intentionally a no-op.
    """
    logger.debug("GPU fan control skipped — laptop GPU fans managed by AWCC.")


def _activate_awcc_fans():
    """Queue AWCC fan max boost on the SensorThread (COM STA owner)."""
    try:
        from core import awcc, sensors
        if not sensors.get_readings().get("awcc_available", False):
            logger.debug("AWCC WMI unavailable — chassis fans not boosted")
            return
        def _do():
            if awcc.turbo_cool_activate():
                logger.info("AWCC fans: max boost active via WMI")
            else:
                logger.warning("AWCC turbo cool: WMI boost returned failure")
        awcc.enqueue(_do)
    except Exception as e:
        logger.debug("AWCC fan boost error: %s", e)


def _restore_fans():
    """Queue AWCC fan restore on the SensorThread (COM STA owner)."""
    _set_gpu_fan(0)
    try:
        from core import awcc, sensors
        if not sensors.get_readings().get("awcc_available", False):
            logger.debug("AWCC WMI unavailable — fan restore skipped")
            return
        def _do():
            awcc.turbo_cool_deactivate()
            logger.info("AWCC fans: restored via WMI")
        awcc.enqueue(_do)
    except Exception as e:
        logger.debug("AWCC fan restore error: %s", e)
