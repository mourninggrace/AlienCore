"""
AlienCore - cpu_voltage.py
Dynamic CPU voltage offset (undervolt / overvolt).

Intel's MSR 0x150 ("OC Mailbox") lets software apply a signed voltage
offset per voltage plane:

  plane 0  CPU core
  plane 1  iGPU
  plane 2  cache / ring (uncore)
  plane 3  system agent
  plane 4  analog I/O

Dell ships Alienware and XPS laptops with Plundervolt mitigation
(CVE-2019-11157) fully enabled in BIOS — MSR 0x150 writes are silently
accepted and dropped.  On unlocked systems (most desktops, many ASUS /
MSI / Gigabyte boards, and some non-Dell laptops) the writes actually
stick and the offsets take effect immediately.

This module:
  * reads the hardware cache to figure out vendor / family
  * advertises capability via is_supported() / get_status()
  * exposes set_offset(plane, offset_mv) with the right validation,
    range clamping, and result reporting

The actual MSR write is gated behind _msr_write(), which is currently a
stub that returns (False, "MSR access not yet implemented").  The
wiring everywhere else — the AI tool, the RISK_HIGH disclaimer dialog,
capability reporting in settings — works today, so adding the MSR
backend later only touches one function.

SAFETY: a negative offset too aggressive for the silicon will hard-
freeze the machine, corrupt in-flight disk writes, or in rare cases
stress the CPU.  Settings revert on reboot, which is the usual escape
hatch.  The AI chat's RISK_HIGH confirm dialog shows the full scope +
liability clause before any call lands here.
"""
# copykitten

import logging
import platform

logger = logging.getLogger("aliencore.cpu_voltage")

# Voltage planes on Intel MSR 0x150 (same IDs as ThrottleStop / Intel XTU)
PLANES = {
    "core":         0,
    "igpu":         1,
    "cache":        2,
    "system_agent": 3,
    "analog_io":    4,
}

# Safe offset range (millivolts).  Real silicon tolerates -200 .. +100 mv
# but exposing the full range invites chaos; the AI tool clamps to these
# conservative bounds.  A user who needs more can edit config directly.
OFFSET_MIN_MV = -150
OFFSET_MAX_MV =  100


# ─────────────────────────────────────────────────────────────────────────────
# Capability detection
# ─────────────────────────────────────────────────────────────────────────────

def _cpu_info() -> dict:
    """Return vendor / family / name from the hardware cache, with fallbacks.

    The hardware cache exposes `is_intel` / `is_amd` booleans rather than a
    free-form vendor string, so we synthesize a vendor label from those
    flags for display.  Using the flags directly (rather than parsing
    cpu["name"]) keeps us aligned with the rest of the codebase.
    """
    try:
        from core import hardware
        hw  = hardware.get_cached() or {}
        cpu = hw.get("cpu", {}) or {}
    except Exception:
        cpu = {}
    is_intel = bool(cpu.get("is_intel"))
    is_amd   = bool(cpu.get("is_amd"))
    if is_intel:
        vendor = "intel"
    elif is_amd:
        vendor = "amd"
    else:
        vendor = ""
    return {
        "vendor":   vendor,
        "is_intel": is_intel,
        "is_amd":   is_amd,
        "name":     cpu.get("name", ""),
        "family":   cpu.get("family", ""),
    }


def _oem_is_locked() -> tuple[bool, str]:
    """Detect OEMs known to hard-lock voltage offsets in BIOS.

    The hardware cache exposes a `platform.is_alienware` flag (set from
    Win32_ComputerSystem model/manufacturer) — use it directly rather
    than re-parsing WMI strings here.
    """
    try:
        from core import hardware
        hw       = hardware.get_cached() or {}
        platform = hw.get("platform", {}) or {}
    except Exception:
        platform = {}

    if platform.get("is_alienware"):
        return True, ("Dell / Alienware firmware locks MSR 0x150 writes "
                      "(Plundervolt mitigation enabled, no BIOS option to "
                      "disable). Voltage offsets are not available on this "
                      "system.")
    return False, ""


def get_status() -> dict:
    """Machine-readable capability / status snapshot."""
    info       = _cpu_info()
    oem_locked, oem_reason = _oem_is_locked()

    supported  = True
    reason     = ""
    if info["is_amd"]:
        supported = False
        reason    = ("Intel MSR 0x150 is not applicable on AMD CPUs — use "
                     "the AMD Curve Optimizer tool (set_amd_curve_optimizer) "
                     "for per-core voltage offsets instead.")
    elif not info["is_intel"] and info["name"]:
        supported = False
        reason    = (f"CPU '{info['name']}' is not a recognized Intel part; "
                     "MSR 0x150 voltage offsets require an Intel CPU.")
    elif oem_locked:
        supported = False
        reason    = oem_reason

    return {
        "supported":    supported,
        "reason":       reason,
        "cpu_vendor":   info["vendor"],
        "cpu_name":     info["name"],
        "planes":       list(PLANES),
        "offset_range": [OFFSET_MIN_MV, OFFSET_MAX_MV],
    }


def is_supported() -> bool:
    return get_status()["supported"]


def status_text() -> str:
    s = get_status()
    if s["supported"]:
        return (f"Voltage offsets available on {s['cpu_name']}. "
                f"Allowed range: {OFFSET_MIN_MV}..{OFFSET_MAX_MV} mV per plane.")
    return f"Voltage offsets NOT available: {s['reason']}"


# ─────────────────────────────────────────────────────────────────────────────
# Offset API
# ─────────────────────────────────────────────────────────────────────────────

def set_offset(plane: str, offset_mv: int) -> tuple[bool, str]:
    """Apply a signed voltage offset to the named plane.

    Returns (ok, message).  On supported hardware this writes MSR 0x150
    via WinRing0 (shared with LibreHardwareMonitor).  On Dell / non-
    Intel systems it short-circuits with an explanation.
    """
    status = get_status()
    if not status["supported"]:
        return False, status["reason"]

    if plane not in PLANES:
        return False, (f"Unknown voltage plane '{plane}'. "
                       f"Valid planes: {', '.join(PLANES)}.")

    try:
        offset_mv = int(offset_mv)
    except (TypeError, ValueError):
        return False, f"offset_mv must be an integer (got {offset_mv!r})."

    if not (OFFSET_MIN_MV <= offset_mv <= OFFSET_MAX_MV):
        return False, (f"Offset {offset_mv} mV out of range "
                       f"({OFFSET_MIN_MV}..{OFFSET_MAX_MV}). Reject.")

    ok, detail = _msr_write(PLANES[plane], offset_mv)
    if not ok:
        return False, detail

    logger.info("CPU voltage offset applied: plane=%s offset_mv=%d",
                plane, offset_mv)
    return True, f"Applied {offset_mv:+d} mV offset to {plane} plane."


# ─────────────────────────────────────────────────────────────────────────────
# MSR backend — stubbed
# ─────────────────────────────────────────────────────────────────────────────
#
# A working backend has to:
#   1. Open a handle to WinRing0.sys (already loaded by lhm_bridge — we
#      can reuse its driver rather than loading a second copy)
#   2. Encode the MSR 0x150 OC-Mailbox command:
#        bit 63     = 1 (command valid)
#        bit 42..36 = plane index (core=0, igpu=1, cache=2, sa=3, aio=4)
#        bit 35..32 = command (0x11 = write offset, 0x10 = read)
#        bit 31..21 = signed voltage offset in 1/1.024 mV units (2's complement)
#      then WRMSR 0x150, followed by RDMSR 0x150 to confirm the write.
#   3. Handle per-core vs. package scope — OC mailbox is package-scoped on
#      all desktop/mobile SKUs since Haswell, so we can target CPU 0.
#
# Until that lands, this stub returns a clear message so the AI tool can
# report the unsupported state honestly instead of silently "succeeding".
def _msr_write(plane_id: int, offset_mv: int) -> tuple[bool, str]:
    return False, ("MSR 0x150 backend not yet implemented in AlienCore. "
                   "Scaffolding is in place — add the WinRing0 IOCTL code "
                   "in core/cpu_voltage.py::_msr_write() to enable writes "
                   "on unlocked systems.")
