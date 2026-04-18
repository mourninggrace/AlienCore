"""
AlienCore - amd_gpu_tuning.py
Runtime tuning for AMD Radeon GPUs via the ADLX SDK.

AMD publishes two control libraries on Windows:

  ADL  (legacy, shipped with Catalyst / Crimson drivers)
  ADLX (modern, shipped with Adrenalin 22.x+ — C++/C# SDK with a COM
        interop layer, covers all RDNA1 / RDNA2 / RDNA3 cards)

ADLX is the supported path going forward.  The relevant interfaces:

  IADLXGPUTuning1          — query tuning capability for each GPU
    ├── GetManualGFXTuning1        ← core clock frequency/voltage control
    ├── GetManualVRAMTuning1       ← memory clock/voltage control
    ├── GetManualPowerTuning       ← power limit slider (0..±20 % typical)
    ├── GetManualFanTuning         ← fan curve / speed override
    └── IsSupportedManual*Tuning   ← presence flag per feature

  IADLXGPUTuningChangedListener — push event when any tuning setting
    moves (useful so AlienCore's sensor bar reflects the new limit).

Each call path opens an ADLX helper COM server, grabs the system-level
tuning service, enumerates GPUs, picks the target GPU's interface, reads
the capability struct, validates the requested value against the
firmware-reported min / max, then commits.  Because ADLX returns rich
per-card range info at runtime (unlike NVIDIA where we clamp at a fixed
conservative range), we don't hard-code ranges here — we ask the card.

PRIORITY HARDWARE: Radeon RX 6000 / 7000 / 9000 series (RDNA2/3/4).
    RDNA1 (5000 series) is supported too, but some SKUs fuse-off the
    voltage control path.

DEPENDENCIES:
    ADLX runtime DLLs (amdadlx64.dll) — installed with Adrenalin driver.
    Python binding: either pythonnet with the ADLX C# wrapper, or
    ctypes against the exported C helper functions from amdadlx64.dll.
    Neither is trivial — this module scaffolds the full tool surface
    and leaves the ADLX plumbing to _adlx_call() which is stubbed.

SAFETY: GPU memory overclocks usually manifest as corruption / TDR,
not hardware damage.  Elevated power limits on cards with weak VRM
cooling (budget or reference boards) can cook MOSFETs over time.  The
AI tool gates all writes behind the RISK_HIGH disclaimer dialog.
"""
# copykitten

import logging

logger = logging.getLogger("aliencore.amd_gpu_tuning")

# Conservative fallback ranges used when ADLX is unreachable but we still
# want the tool schema to reflect something sensible.  Real ranges come
# from ADLX GetMaxCoreClock / GetMinCoreClock at write time.
CORE_CLOCK_FALLBACK_MIN_MHZ  = -200
CORE_CLOCK_FALLBACK_MAX_MHZ  =  300
MEM_CLOCK_FALLBACK_MIN_MHZ   = -200
MEM_CLOCK_FALLBACK_MAX_MHZ   =  400
POWER_OFFSET_FALLBACK_MIN_PCT = -30
POWER_OFFSET_FALLBACK_MAX_PCT =  20
FAN_MIN_PCT                  =  0
FAN_MAX_PCT                  =  100


# ─────────────────────────────────────────────────────────────────────────────
# Capability detection
# ─────────────────────────────────────────────────────────────────────────────

def _primary_amd_gpu() -> dict:
    """Return the primary discrete AMD GPU, or {} if none."""
    try:
        from core import hardware
        hw   = hardware.get_cached() or {}
        gpus = hw.get("gpu") or []
    except Exception:
        gpus = []

    for g in gpus:
        if g.get("is_amd") and not g.get("is_integrated"):
            return {"name": g.get("name", ""), "driver": g.get("driver", "")}
    return {}


def _probe_adlx() -> tuple[bool, str]:
    """Return (present, reason) — does ADLX appear to be installed?"""
    import os
    candidates = [
        r"C:\Windows\System32\amdadlx64.dll",
        r"C:\Program Files\AMD\CNext\CNext\amdadlx64.dll",
    ]
    for p in candidates:
        if os.path.isfile(p):
            return True, p
    return False, ("amdadlx64.dll not found — install AMD Adrenalin 22.x or "
                   "newer to enable AMD GPU tuning.")


def get_status() -> dict:
    """Capability snapshot — same shape as gpu_tuning.get_status()['amd']."""
    gpu = _primary_amd_gpu()
    if not gpu:
        return {
            "supported": False,
            "reason":    "No discrete AMD GPU detected.",
        }

    adlx_ok, adlx_reason = _probe_adlx()
    if not adlx_ok:
        return {
            "supported": False,
            "reason":    adlx_reason,
            "gpu_name":  gpu["name"],
        }

    return {
        "supported":     True,
        "reason":        "",
        "gpu_name":      gpu["name"],
        "gpu_driver":    gpu["driver"],
        "adlx_dll":      adlx_reason,   # path we found
        "backend":       "adlx-stub",   # flip to "adlx" once implemented
        "ranges": {
            "core_clock_offset_mhz":   [CORE_CLOCK_FALLBACK_MIN_MHZ,
                                        CORE_CLOCK_FALLBACK_MAX_MHZ],
            "mem_clock_offset_mhz":    [MEM_CLOCK_FALLBACK_MIN_MHZ,
                                        MEM_CLOCK_FALLBACK_MAX_MHZ],
            "power_offset_pct":        [POWER_OFFSET_FALLBACK_MIN_PCT,
                                        POWER_OFFSET_FALLBACK_MAX_PCT],
            "fan_pct":                 [FAN_MIN_PCT, FAN_MAX_PCT],
        },
    }


def is_supported() -> bool:
    return get_status()["supported"]


def status_text() -> str:
    s = get_status()
    if not s["supported"]:
        return f"AMD GPU tuning NOT available: {s['reason']}"
    return (f"AMD GPU tuning detected on {s['gpu_name']} "
            f"(driver {s.get('gpu_driver','?')}).  ADLX backend is "
            "scaffolded but write path not yet implemented.")


# ─────────────────────────────────────────────────────────────────────────────
# Tuning API — all dispatch through _adlx_call() which is stubbed
# ─────────────────────────────────────────────────────────────────────────────

def set_core_clock_offset(offset_mhz: int) -> tuple[bool, str]:
    """Apply a signed GFX clock offset to the primary AMD GPU."""
    s = get_status()
    if not s["supported"]:
        return False, s["reason"]
    try:
        offset_mhz = int(offset_mhz)
    except (TypeError, ValueError):
        return False, f"offset_mhz must be an integer (got {offset_mhz!r})."
    lo, hi = s["ranges"]["core_clock_offset_mhz"]
    if not (lo <= offset_mhz <= hi):
        return False, f"Core clock offset {offset_mhz} MHz out of range ({lo}..{hi})."
    return _adlx_call("ManualGFXTuning.SetCoreClockOffset", {"offset_mhz": offset_mhz})


def set_mem_clock_offset(offset_mhz: int) -> tuple[bool, str]:
    """Apply a signed VRAM clock offset."""
    s = get_status()
    if not s["supported"]:
        return False, s["reason"]
    try:
        offset_mhz = int(offset_mhz)
    except (TypeError, ValueError):
        return False, f"offset_mhz must be an integer (got {offset_mhz!r})."
    lo, hi = s["ranges"]["mem_clock_offset_mhz"]
    if not (lo <= offset_mhz <= hi):
        return False, f"Memory clock offset {offset_mhz} MHz out of range ({lo}..{hi})."
    return _adlx_call("ManualVRAMTuning.SetMemoryClockOffset", {"offset_mhz": offset_mhz})


def set_power_offset(percent: int) -> tuple[bool, str]:
    """Shift the GPU's power slider by a signed percentage (firmware-capped)."""
    s = get_status()
    if not s["supported"]:
        return False, s["reason"]
    try:
        percent = int(percent)
    except (TypeError, ValueError):
        return False, f"percent must be an integer (got {percent!r})."
    lo, hi = s["ranges"]["power_offset_pct"]
    if not (lo <= percent <= hi):
        return False, f"Power offset {percent}% out of range ({lo}..{hi})."
    return _adlx_call("ManualPowerTuning.SetPowerLimitOffset", {"percent": percent})


def set_fan_duty(percent: int) -> tuple[bool, str]:
    """Set a fixed fan duty % (0..100).  Pass percent=-1 to return to auto."""
    s = get_status()
    if not s["supported"]:
        return False, s["reason"]
    try:
        percent = int(percent)
    except (TypeError, ValueError):
        return False, f"percent must be an integer (got {percent!r})."
    if percent == -1:
        return _adlx_call("ManualFanTuning.SetAutoMode", {})
    if not (FAN_MIN_PCT <= percent <= FAN_MAX_PCT):
        return False, f"Fan duty {percent}% out of range (0..100)."
    return _adlx_call("ManualFanTuning.SetFanSpeedPct", {"percent": percent})


# ─────────────────────────────────────────────────────────────────────────────
# ADLX backend — stubbed
# ─────────────────────────────────────────────────────────────────────────────
#
# A working backend needs one of:
#
#   Option A — pythonnet + AMD's C# ADLXPybind wrapper
#     - pip install pythonnet
#     - load AMD's ADLXPybind.dll via clr.AddReference
#     - use Adlx.GetSystemTuningService() → EnumerateGPUs() → GetGPUTuningServices()
#     Pros: closest to the documented ADLX API, easy to translate from AMD's
#           C# samples.  Cons: pulls in .NET runtime, large dep.
#
#   Option B — ctypes against the C helper exports in amdadlx64.dll
#     - ADLX_ReturnCode ADLXHelper_Initialize()
#     - ADLX_ReturnCode ADLXHelper_GetSystemServices(IADLXSystem**)
#     - then COM QueryInterface chain to IADLXGPUTuningServices
#     Pros: zero extra Python deps.  Cons: hand-rolled COM v-tables in ctypes
#           — verbose but tractable, ZenStates-Core / LibreHardwareMonitor
#           have similar ctypes-COM examples to copy.
#
# Whichever option lands, the write flow is:
#   1. Initialize ADLX (once per process; share with sensors if they move to ADLX)
#   2. EnumerateGPUs(), pick GPU with matching PCI bus/device/function
#   3. GetGPUTuningServices() → QueryInterface IADLXGPUTuning1
#   4. For the requested op (e.g. SetCoreClockOffset):
#        a. GetManualGFXTuning1() → IADLXManualGFXTuning1
#        b. GetCoreClockRange() → clamp request to firmware-reported range
#        c. SetCoreClockOffset(value)
#        d. Release() all COM interfaces
#   5. Translate ADLX_ReturnCode → (ok, message)
#
def _adlx_call(op: str, args: dict) -> tuple[bool, str]:
    logger.info("ADLX stub: %s %s", op, args)
    return False, ("AMD ADLX backend not yet implemented in AlienCore.  "
                   "Scaffolding (capability probe, range clamps, tool "
                   "registry wiring) is complete — fill in "
                   "core/amd_gpu_tuning.py::_adlx_call() using pythonnet "
                   "or ctypes COM against amdadlx64.dll to enable writes.")
