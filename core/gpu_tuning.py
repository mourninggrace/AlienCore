"""
AlienCore - gpu_tuning.py
Runtime GPU tuning (NVIDIA + AMD capability dispatch).

NVIDIA PATH — fully implemented, testable today:
    Uses pynvml (nvidia-ml-py) to talk directly to the NVML shared library
    that ships with the NVIDIA driver.  No subprocess, no admin-only COM,
    no third-party tool.  The four primitives exposed:

      nvmlDeviceSetGpcClkVfOffset      core-clock offset   (MHz, signed)
      nvmlDeviceSetMemClkVfOffset      memory-clock offset (MHz, signed)
      nvmlDeviceSetPowerManagementLimit  power cap         (milliwatts)
      nvmlDeviceSetFanSpeed_v2         fan duty            (0..100 %)
      nvmlDeviceSetDefaultFanSpeed_v2  hand control back to driver

    The GPC/Mem offset pair is the same knob MSI Afterburner and GeForce
    Experience's "Automatic Tuning" writes to — a signed offset applied
    across the entire voltage/frequency curve.  Offsets survive until
    reboot (driver resets on boot) unless re-applied.

AMD PATH — delegated to core/amd_gpu_tuning.py (ADLX scaffolding).
INTEL ARC — not yet implemented.

RANGE CLAMPS (conservative — users who want more can edit config.json):
    Core offset:   -200 .. +300 MHz
    Memory offset: -1000 .. +2000 MHz   (memory tolerates bigger shifts)
    Power limit:   clamped to the firmware min/max reported by NVML
    Fan duty:      0 .. 100 %

ADMIN: NVML clock-offset and power-limit writes require administrator
privileges.  AlienCore already runs elevated for registry / power-plan
work, so this is a non-issue.

SAFETY: a too-aggressive memory overclock on GDDR6/GDDR6X usually manifests
as image corruption or driver TDR rather than hardware damage.  A too-low
power limit just caps performance.  The genuine risk surface is elevated
power limits on cards with weak VRM cooling — sustained operation above
rated TGP can cook MOSFETs.  All RISK_HIGH tools gate behind the "I
understand the risks and accept full responsibility" checkbox.
"""
# copykitten

import logging

logger = logging.getLogger("aliencore.gpu_tuning")

# ─── Conservative ranges ────────────────────────────────────────────────────
CORE_OFFSET_MIN_MHZ = -200
CORE_OFFSET_MAX_MHZ =  300
MEM_OFFSET_MIN_MHZ  = -1000
MEM_OFFSET_MAX_MHZ  =  2000
FAN_MIN_PCT         =  0
FAN_MAX_PCT         =  100


# ─────────────────────────────────────────────────────────────────────────────
# Capability detection
# ─────────────────────────────────────────────────────────────────────────────

def _primary_gpu_info() -> dict:
    """Return vendor / name of the primary discrete GPU."""
    try:
        from core import hardware
        hw   = hardware.get_cached() or {}
        gpus = hw.get("gpu") or []
    except Exception:
        gpus = []

    nvidia = next((g for g in gpus if g.get("is_nvidia")), None)
    if nvidia:
        return {"vendor": "nvidia", "name": nvidia.get("name", "")}

    amd = next((g for g in gpus if g.get("is_amd")
                and not g.get("is_integrated")), None)
    if amd:
        return {"vendor": "amd", "name": amd.get("name", "")}

    if gpus:
        g = gpus[0]
        vendor = "intel" if "intel" in (g.get("name") or "").lower() else "unknown"
        return {"vendor": vendor, "name": g.get("name", "")}

    return {"vendor": "unknown", "name": ""}


def get_status() -> dict:
    """Capability snapshot — used by the AI tool to pick the right write path."""
    info    = _primary_gpu_info()
    nvidia  = _nvidia_status() if info["vendor"] == "nvidia" else {
        "supported": False,
        "reason":    "Primary GPU is not NVIDIA.",
    }

    try:
        from core import amd_gpu_tuning
        amd = amd_gpu_tuning.get_status()
    except Exception as e:
        amd = {"supported": False, "reason": f"amd_gpu_tuning import failed: {e}"}

    return {
        "primary_vendor": info["vendor"],
        "primary_name":   info["name"],
        "nvidia":         nvidia,
        "amd":            amd,
        "ranges": {
            "core_offset_mhz":  [CORE_OFFSET_MIN_MHZ, CORE_OFFSET_MAX_MHZ],
            "mem_offset_mhz":   [MEM_OFFSET_MIN_MHZ,  MEM_OFFSET_MAX_MHZ],
            "fan_pct":          [FAN_MIN_PCT,         FAN_MAX_PCT],
        },
    }


def status_text() -> str:
    s = get_status()
    lines = [f"Primary GPU: {s['primary_name'] or 'unknown'} "
             f"({s['primary_vendor']})."]

    nv = s["nvidia"]
    if nv["supported"]:
        lines.append(f"NVIDIA tuning: available via NVML. "
                     f"Core offset {CORE_OFFSET_MIN_MHZ:+d}..{CORE_OFFSET_MAX_MHZ:+d} MHz, "
                     f"Mem offset {MEM_OFFSET_MIN_MHZ:+d}..{MEM_OFFSET_MAX_MHZ:+d} MHz, "
                     f"power {nv.get('power_limit_min_w','?')}..{nv.get('power_limit_max_w','?')} W.")
    else:
        lines.append(f"NVIDIA tuning: not available ({nv['reason']}).")

    amd = s["amd"]
    if amd["supported"]:
        lines.append(f"AMD tuning: detected {amd.get('gpu_name','')} — "
                     "ADLX backend scaffolded (not yet writing).")
    elif s["primary_vendor"] == "amd":
        lines.append(f"AMD tuning: {amd['reason']}")

    return " ".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# NVIDIA — pynvml backend
# ─────────────────────────────────────────────────────────────────────────────

def _nvml():
    """Return (pynvml_module, handle) or raise."""
    import pynvml
    pynvml.nvmlInit()
    h = pynvml.nvmlDeviceGetHandleByIndex(0)
    return pynvml, h


def _nvidia_status() -> dict:
    """Probe pynvml + power limit constraints.  No writes."""
    result = {
        "supported":         False,
        "reason":            "",
        "gpu_name":          "",
        "driver":            "",
        "power_limit_min_w": None,
        "power_limit_max_w": None,
        "power_limit_cur_w": None,
    }
    try:
        pynvml, h = _nvml()
    except Exception as e:
        result["reason"] = f"pynvml unavailable: {e}"
        return result

    try:
        result["gpu_name"] = pynvml.nvmlDeviceGetName(h)
        if isinstance(result["gpu_name"], bytes):
            result["gpu_name"] = result["gpu_name"].decode("ascii", "replace")
    except Exception:
        pass
    try:
        result["driver"] = pynvml.nvmlSystemGetDriverVersion()
        if isinstance(result["driver"], bytes):
            result["driver"] = result["driver"].decode("ascii", "replace")
    except Exception:
        pass

    try:
        lo, hi = pynvml.nvmlDeviceGetPowerManagementLimitConstraints(h)
        result["power_limit_min_w"] = round(lo / 1000.0, 1)
        result["power_limit_max_w"] = round(hi / 1000.0, 1)
    except Exception as e:
        logger.debug("power-limit constraints unavailable: %s", e)

    try:
        cur = pynvml.nvmlDeviceGetPowerManagementLimit(h)
        result["power_limit_cur_w"] = round(cur / 1000.0, 1)
    except Exception:
        pass

    result["supported"] = True
    return result


def _clamp(val: int, lo: int, hi: int) -> tuple[bool, int]:
    return (lo <= val <= hi), int(val)


def _nvml_call(fn_name: str, *args) -> tuple[bool, str]:
    """Call an NVML write function with error translation."""
    try:
        pynvml, h = _nvml()
    except Exception as e:
        return False, f"pynvml unavailable: {e}"

    fn = getattr(pynvml, fn_name, None)
    if fn is None:
        return False, f"NVML missing {fn_name} — update the driver / nvidia-ml-py."

    try:
        fn(h, *args)
        return True, "OK"
    except Exception as e:
        # pynvml raises NVMLError subclasses with descriptive strings
        return False, f"{fn_name} failed: {e}"


def set_core_offset(offset_mhz: int) -> tuple[bool, str]:
    """Apply a signed core-clock VF offset (NVIDIA only)."""
    if _primary_gpu_info()["vendor"] != "nvidia":
        return False, "Primary GPU is not NVIDIA — use set_amd_gpu_* tools."

    try:
        offset_mhz = int(offset_mhz)
    except (TypeError, ValueError):
        return False, f"offset_mhz must be an integer (got {offset_mhz!r})."

    ok, offset_mhz = _clamp(offset_mhz, CORE_OFFSET_MIN_MHZ, CORE_OFFSET_MAX_MHZ)
    if not ok:
        return False, (f"Core offset {offset_mhz} MHz out of range "
                       f"({CORE_OFFSET_MIN_MHZ}..{CORE_OFFSET_MAX_MHZ}).")

    ok, msg = _nvml_call("nvmlDeviceSetGpcClkVfOffset", offset_mhz)
    if not ok:
        return False, msg
    logger.info("NVIDIA core-clock offset applied: %+d MHz", offset_mhz)
    return True, f"Applied {offset_mhz:+d} MHz core-clock offset."


def set_mem_offset(offset_mhz: int) -> tuple[bool, str]:
    """Apply a signed memory-clock VF offset (NVIDIA only)."""
    if _primary_gpu_info()["vendor"] != "nvidia":
        return False, "Primary GPU is not NVIDIA — use set_amd_gpu_* tools."

    try:
        offset_mhz = int(offset_mhz)
    except (TypeError, ValueError):
        return False, f"offset_mhz must be an integer (got {offset_mhz!r})."

    ok, offset_mhz = _clamp(offset_mhz, MEM_OFFSET_MIN_MHZ, MEM_OFFSET_MAX_MHZ)
    if not ok:
        return False, (f"Memory offset {offset_mhz} MHz out of range "
                       f"({MEM_OFFSET_MIN_MHZ}..{MEM_OFFSET_MAX_MHZ}).")

    ok, msg = _nvml_call("nvmlDeviceSetMemClkVfOffset", offset_mhz)
    if not ok:
        return False, msg
    logger.info("NVIDIA memory-clock offset applied: %+d MHz", offset_mhz)
    return True, f"Applied {offset_mhz:+d} MHz memory-clock offset."


def set_power_limit(watts: float) -> tuple[bool, str]:
    """Set the enforced GPU power limit (NVIDIA only, in watts)."""
    if _primary_gpu_info()["vendor"] != "nvidia":
        return False, "Primary GPU is not NVIDIA — use set_amd_gpu_* tools."

    try:
        watts = float(watts)
    except (TypeError, ValueError):
        return False, f"watts must be a number (got {watts!r})."

    nv = _nvidia_status()
    lo = nv.get("power_limit_min_w")
    hi = nv.get("power_limit_max_w")
    if lo is not None and hi is not None and not (lo <= watts <= hi):
        return False, (f"Power limit {watts:g} W out of firmware range "
                       f"({lo:g}..{hi:g} W).")

    ok, msg = _nvml_call("nvmlDeviceSetPowerManagementLimit", int(round(watts * 1000)))
    if not ok:
        return False, msg
    logger.info("NVIDIA power limit set to %.1f W", watts)
    return True, f"Set GPU power limit to {watts:g} W."


def set_fan_duty(percent: int, fan_index: int = 0) -> tuple[bool, str]:
    """Set a fixed fan duty %.  Pass percent=-1 to return control to the driver."""
    if _primary_gpu_info()["vendor"] != "nvidia":
        return False, "Primary GPU is not NVIDIA — use set_amd_gpu_* tools."

    try:
        percent   = int(percent)
        fan_index = int(fan_index)
    except (TypeError, ValueError):
        return False, "percent / fan_index must be integers."

    if percent == -1:
        ok, msg = _nvml_call("nvmlDeviceSetDefaultFanSpeed_v2", fan_index)
        if not ok:
            return False, msg
        logger.info("NVIDIA fan #%d returned to driver control", fan_index)
        return True, f"Fan #{fan_index} handed back to driver control."

    ok, percent = _clamp(percent, FAN_MIN_PCT, FAN_MAX_PCT)
    if not ok:
        return False, f"Fan duty {percent}% out of range (0..100)."

    ok, msg = _nvml_call("nvmlDeviceSetFanSpeed_v2", fan_index, percent)
    if not ok:
        return False, msg
    logger.info("NVIDIA fan #%d set to %d%%", fan_index, percent)
    return True, f"Fan #{fan_index} set to {percent}%."
