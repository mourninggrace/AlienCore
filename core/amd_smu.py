"""
AlienCore - amd_smu.py
Reads AMD CPU telemetry via the Ryzen Master Platform.dll interface.

Works on systems where Memory Integrity (HVCI) blocks WinRing0/LHM Ring0 access.
AMDRyzenMasterDriverVxx is HVCI-compatible and already running when Ryzen Master
is installed.  Platform.dll wraps it and exposes C-callable functions.

Struct layout of the GetRmCpuParameters output buffer (reverse-engineered):
  offset 16: float  Tctl limit or max temp threshold
  offset 20: float  Tdie — live CPU package temperature in °C  ← primary value
  offset 28: float  secondary thermal threshold
  offset 40: float  critical temp threshold (~95°C)
  offset 56: float  warning temp threshold (~80°C)
  offset 88: float  peak boost clock (MHz)
  offset 108: float CPU VID / VDDCR voltage
  Sentinel value -1.0 means "not available" for that field.
"""

import ctypes
import logging
import os
import struct
import threading

logger = logging.getLogger("aliencore.amd_smu")

_RYZEN_BIN = r"C:\Program Files\AMD\RyzenMaster\bin"
_BUF_SIZE  = 512

_lock      = threading.Lock()
_init_done = False
_platform  = None   # ctypes.CDLL for Platform.dll
_func      = None   # GetRmCpuParameters


def _init() -> bool:
    global _init_done, _platform, _func
    if _init_done:
        return _platform is not None
    _init_done = True

    if not os.path.isdir(_RYZEN_BIN):
        logger.debug("Ryzen Master not found — AMD SMU bridge unavailable")
        return False

    try:
        os.add_dll_directory(_RYZEN_BIN)
        # Device.dll must load first (Platform.dll imports it)
        ctypes.CDLL(os.path.join(_RYZEN_BIN, "Device.dll"))
        _platform = ctypes.CDLL(os.path.join(_RYZEN_BIN, "Platform.dll"))
        fn = _platform.GetRmCpuParameters
        fn.restype   = ctypes.c_bool
        fn.argtypes  = [ctypes.POINTER(ctypes.c_ubyte)]
        _func = fn
        logger.info("AMD SMU bridge ready via Ryzen Master Platform.dll")
        return True
    except Exception as e:
        logger.warning("AMD SMU bridge init failed: %s", e)
        _platform = None
        return False


def get_cpu_temp() -> float | None:
    """
    Return live CPU Tdie temperature in °C, or None if unavailable.
    Reads from GetRmCpuParameters offset 20.
    """
    with _lock:
        if not _init():
            return None
        try:
            buf = (ctypes.c_ubyte * _BUF_SIZE)()
            _func(buf)
            raw  = bytes(buf)
            val  = struct.unpack_from("<f", raw, 20)[0]
            if val <= 0 or val > 120:
                return None
            return round(float(val), 1)
        except Exception as e:
            logger.debug("AMD SMU temp read error: %s", e)
            return None


def get_boost_clock_mhz() -> float | None:
    """Return current peak boost clock in MHz, or None."""
    with _lock:
        if not _init():
            return None
        try:
            buf = (ctypes.c_ubyte * _BUF_SIZE)()
            _func(buf)
            raw = bytes(buf)
            val = struct.unpack_from("<f", raw, 88)[0]
            if val <= 0 or val == -1.0:
                return None
            return round(float(val), 0)
        except Exception as e:
            logger.debug("AMD SMU clock read error: %s", e)
            return None


def get_cpu_voltage() -> float | None:
    """Return CPU VID voltage, or None."""
    with _lock:
        if not _init():
            return None
        try:
            buf = (ctypes.c_ubyte * _BUF_SIZE)()
            _func(buf)
            raw = bytes(buf)
            val = struct.unpack_from("<f", raw, 108)[0]
            if val <= 0 or val == -1.0 or val > 3.0:
                return None
            return round(float(val), 4)
        except Exception as e:
            logger.debug("AMD SMU voltage read error: %s", e)
            return None
