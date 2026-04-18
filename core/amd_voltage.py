"""
AlienCore - amd_voltage.py
Dynamic voltage / PBO control for AMD Ryzen (Zen 2 / Zen 3 / Zen 4 / Zen 5).

Unlike Intel's MSR 0x150 OC-Mailbox, AMD Ryzen exposes voltage-frequency
tuning through the **SMU (System Management Unit) mailbox**.  The mailbox
lives in PCI configuration space on the SoC and is accessed via indirect
register pair — one command register, one argument array, one response
register.  Each Zen generation has its own mailbox base and command IDs.

Two useful tuning surfaces are exposed to userspace tools:

  1. CURVE OPTIMIZER (Zen 3+) — per-core voltage-frequency curve offset.
     One signed "count" ≈ 3–5 mV of negative offset (undervolt), 1 count
     positive is rare but accepted.  Range is typically -30..+30 counts
     per core; ~-15 is the "easy undervolt" sweet spot for most silicon.
     SMU command on Zen 3 desktop (Vermeer) = 0x35 (SetOverclockMargin),
     with argument packed as (core_index << 20) | (count & 0xFFFF).

  2. PBO LIMITS — Precision Boost Overdrive power/current caps:
       PPT (Package Power Tracking)     in mW   — SMU cmd 0x53 (Vermeer)
       TDC (Thermal Design Current)     in mA   — SMU cmd 0x54
       EDC (Electrical Design Current)  in mA   — SMU cmd 0x55
     These mirror the PBO sliders in the BIOS.  Setting a limit to 0 tells
     the firmware to use motherboard defaults.

SUPPORTED HARDWARE (priority):
    AMD Ryzen 9 5950X    — Zen 3 / Vermeer / AM4  — 2 CCD × 8 cores
    AMD Ryzen 9 5900X    — Zen 3 / Vermeer / AM4  — 2 CCD × 6 cores
  both use the Vermeer SMU mailbox and Curve Optimizer command set.

Zen 2 (Matisse) accepts PBO limits but NOT Curve Optimizer.
Zen 4 (Raphael) and Zen 5 (Granite Ridge) use different SMU command IDs —
the scaffolding here isolates them behind _resolve_smu() so adding support
is a matter of filling in that lookup table.

DEPENDENCIES:
    WinRing0.sys kernel driver — already loaded by the LHM bridge, can be
    shared for MSR/PCI access.  An alternative on locked systems is the
    AMD RyzenAdj DLL (LGPL), but it's laptop-oriented (STAPM / slow-PPT /
    fast-PPT tuning, no desktop Curve Optimizer).

SAFETY:
    A too-aggressive per-core curve undervolt will cause idle WHEA errors,
    mid-load hard-hangs, or corrupted in-flight disk writes.  A too-high
    PBO limit will not damage the silicon directly (firmware still caps at
    FIT voltage) but can cook VRMs on budget motherboards.  All settings
    revert on reboot.  The AI tool layer pops the RISK_HIGH disclaimer
    dialog before any call reaches set_curve_offset() / set_pbo_limit().
"""
# copykitten

import logging

logger = logging.getLogger("aliencore.amd_voltage")

# ─────────────────────────────────────────────────────────────────────────────
# Curve Optimizer range (per-core, in SMU "counts", ~3–5 mV each)
# ─────────────────────────────────────────────────────────────────────────────

CURVE_MIN_COUNTS = -30
CURVE_MAX_COUNTS =  30

# Mailbox command IDs per Zen generation.  Populated from community research
# (ZenStates-Core, PBO2Tuner source, pjakobs/amd-curve-optimizer).  When the
# SMU write path is implemented, _resolve_smu() picks the right row.
_SMU_TABLE = {
    # family → {"curve_offset": cmd, "ppt_mw": cmd, "tdc_ma": cmd, "edc_ma": cmd,
    #           "mailbox_base": pci_offset}
    "zen3": {
        "curve_offset": 0x35,
        "ppt_mw":       0x53,
        "tdc_ma":       0x54,
        "edc_ma":       0x55,
        "mailbox_base": 0x03B10524,   # Vermeer SMU mailbox (PCI idx/data pair)
    },
    "zen4": {
        "curve_offset": 0x36,         # Raphael uses a different ID
        "ppt_mw":       0x57,
        "tdc_ma":       0x58,
        "edc_ma":       0x59,
        "mailbox_base": 0x03B10524,
    },
    # Zen 2 / Matisse: Curve Optimizer NOT supported by silicon — PBO only.
    "zen2": {
        "curve_offset": None,
        "ppt_mw":       0x51,
        "tdc_ma":       0x52,
        "edc_ma":       0x53,
        "mailbox_base": 0x03B10524,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Capability detection
# ─────────────────────────────────────────────────────────────────────────────

def _cpu_info() -> dict:
    try:
        from core import hardware
        hw  = hardware.get_cached() or {}
        cpu = hw.get("cpu", {}) or {}
    except Exception:
        cpu = {}
    return {
        "vendor": (cpu.get("vendor") or "").lower(),
        "name":   cpu.get("name", ""),
        "family": cpu.get("family", ""),
        "is_amd": bool(cpu.get("is_amd")),
    }


def _detect_zen_gen(cpu_name: str) -> str | None:
    """Map CPU model name to a Zen generation key ('zen2'..'zen5').

    Returns None if the model can't be placed — safer than guessing,
    since SMU command IDs differ per generation.
    """
    n = cpu_name.lower()

    # Zen 5 — Granite Ridge (9000 series desktop)
    if any(m in n for m in ("ryzen 9 9950", "ryzen 9 9900",
                            "ryzen 7 9800", "ryzen 7 9700",
                            "ryzen 5 9600")):
        return "zen5"

    # Zen 4 — Raphael (7000 non-X3D and X3D)
    if any(m in n for m in ("ryzen 9 7950", "ryzen 9 7900",
                            "ryzen 7 7800", "ryzen 7 7700",
                            "ryzen 5 7600")):
        return "zen4"

    # Zen 3 — Vermeer (5000 series, incl. priority SKUs 5950X / 5900X)
    if any(m in n for m in ("ryzen 9 5950", "ryzen 9 5900",
                            "ryzen 7 5800", "ryzen 7 5700",
                            "ryzen 5 5600", "ryzen 5 5500")):
        return "zen3"

    # Zen 2 — Matisse (3000 series desktop, excl. 3400G/3200G APUs)
    if any(m in n for m in ("ryzen 9 3950", "ryzen 9 3900",
                            "ryzen 7 3800", "ryzen 7 3700",
                            "ryzen 5 3600")):
        return "zen2"

    return None


def _resolve_smu() -> dict | None:
    """Return the SMU command row for the running CPU, or None if unmappable."""
    info = _cpu_info()
    if not info["is_amd"]:
        return None
    gen = _detect_zen_gen(info["name"])
    if gen is None:
        return None
    return _SMU_TABLE.get(gen)


def get_status() -> dict:
    """Machine-readable capability / status snapshot."""
    info = _cpu_info()

    supported = False
    reason    = ""
    zen_gen   = None
    cmds      = None

    if not info["is_amd"]:
        reason = (f"CPU vendor is not AMD (detected '{info['vendor']}'). "
                  "AMD voltage control requires a Ryzen CPU.")
    else:
        zen_gen = _detect_zen_gen(info["name"])
        if zen_gen is None:
            reason = (f"Could not map '{info['name']}' to a known Zen "
                      "generation.  Add an entry to _detect_zen_gen() in "
                      "core/amd_voltage.py if your CPU is missing.")
        else:
            cmds = _SMU_TABLE.get(zen_gen)
            if cmds is None:
                reason = f"No SMU command table for {zen_gen}."
            else:
                supported = True

    return {
        "supported":    supported,
        "reason":       reason,
        "cpu_vendor":   info["vendor"],
        "cpu_name":     info["name"],
        "zen_gen":      zen_gen,
        "curve_range":  [CURVE_MIN_COUNTS, CURVE_MAX_COUNTS],
        "curve_supported": bool(cmds and cmds.get("curve_offset") is not None),
        "pbo_supported":   bool(cmds and cmds.get("ppt_mw")       is not None),
    }


def is_supported() -> bool:
    return get_status()["supported"]


def status_text() -> str:
    s = get_status()
    if not s["supported"]:
        return f"AMD voltage / PBO NOT available: {s['reason']}"
    parts = [f"AMD {s['zen_gen']} detected ({s['cpu_name']})."]
    if s["curve_supported"]:
        parts.append(f"Curve Optimizer: {CURVE_MIN_COUNTS}..{CURVE_MAX_COUNTS} counts per core.")
    else:
        parts.append("Curve Optimizer: not supported on this Zen generation.")
    if s["pbo_supported"]:
        parts.append("PBO limits (PPT / TDC / EDC): available.")
    return " ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Curve Optimizer — per-core voltage/frequency offset
# ─────────────────────────────────────────────────────────────────────────────

def set_curve_offset(core_index: int, counts: int) -> tuple[bool, str]:
    """Apply a signed Curve Optimizer offset to a specific physical core.

    Negative counts = undervolt (most common); each count ≈ 3–5 mV.
    """
    status = get_status()
    if not status["supported"]:
        return False, status["reason"]
    if not status["curve_supported"]:
        return False, (f"Curve Optimizer is not supported on "
                       f"{status['zen_gen']} — PBO limits only.")

    try:
        core_index = int(core_index)
        counts     = int(counts)
    except (TypeError, ValueError):
        return False, "core_index and counts must be integers."

    if core_index < 0:
        return False, "core_index must be >= 0."

    if not (CURVE_MIN_COUNTS <= counts <= CURVE_MAX_COUNTS):
        return False, (f"Curve offset {counts} out of range "
                       f"({CURVE_MIN_COUNTS}..{CURVE_MAX_COUNTS}). Reject.")

    cmds = _resolve_smu()
    arg  = ((core_index & 0xFFF) << 20) | (counts & 0xFFFF)
    ok, detail = _smu_write(cmds["curve_offset"], [arg])
    if not ok:
        return False, detail
    logger.info("Curve Optimizer applied: core=%d counts=%d", core_index, counts)
    return True, f"Applied Curve Optimizer {counts:+d} to core #{core_index}."


def set_curve_all_cores(counts: int) -> tuple[bool, str]:
    """Apply the same Curve Optimizer offset to every physical core.

    Simpler than per-core tuning; useful as a conservative 'all-core
    undervolt'.  Fails atomically on the first rejected core.
    """
    status = get_status()
    if not status["supported"] or not status["curve_supported"]:
        return False, status["reason"] or "Curve Optimizer unsupported."

    try:
        import psutil
        phys = psutil.cpu_count(logical=False) or 1
    except Exception:
        phys = 1

    for core in range(phys):
        ok, msg = set_curve_offset(core, counts)
        if not ok:
            return False, f"Failed at core #{core}: {msg}"
    return True, f"Applied {counts:+d} Curve Optimizer offset to all {phys} cores."


# ─────────────────────────────────────────────────────────────────────────────
# PBO limits — PPT / TDC / EDC
# ─────────────────────────────────────────────────────────────────────────────

_PBO_LIMITS = {
    # name: (smu_key, unit, min, max)
    "ppt": ("ppt_mw", "mW", 0, 1_000_000),   # up to 1000 W package
    "tdc": ("tdc_ma", "mA", 0,   500_000),   # up to 500 A thermal design current
    "edc": ("edc_ma", "mA", 0,   500_000),   # up to 500 A electrical design current
}


def set_pbo_limit(kind: str, value: int) -> tuple[bool, str]:
    """Set one PBO limit (kind='ppt' / 'tdc' / 'edc').  Value of 0 = mobo default."""
    status = get_status()
    if not status["supported"] or not status["pbo_supported"]:
        return False, status["reason"] or "PBO unsupported."

    kind = (kind or "").lower()
    if kind not in _PBO_LIMITS:
        return False, f"Unknown PBO limit '{kind}'. Valid: {', '.join(_PBO_LIMITS)}."

    smu_key, unit, lo, hi = _PBO_LIMITS[kind]
    try:
        value = int(value)
    except (TypeError, ValueError):
        return False, f"value must be an integer (got {value!r})."

    if not (lo <= value <= hi):
        return False, f"{kind.upper()} value {value} {unit} out of range ({lo}..{hi})."

    cmds = _resolve_smu()
    ok, detail = _smu_write(cmds[smu_key], [value])
    if not ok:
        return False, detail
    logger.info("PBO %s set to %d %s", kind.upper(), value, unit)
    return True, f"Set PBO {kind.upper()} = {value} {unit}."


# ─────────────────────────────────────────────────────────────────────────────
# SMU backend — stubbed
# ─────────────────────────────────────────────────────────────────────────────
#
# Implementing _smu_write() requires:
#   1. Shared WinRing0 handle (already opened by lhm_bridge — add a small
#      shim in core/lhm_manager.py to expose it, or reload by WinRing0.sys
#      service name)
#   2. PCI indirect access to the SMU mailbox:
#        a. Write mailbox-base + 0x4 ← RESPONSE_BUSY (0x01) to clear state
#        b. Write mailbox-base + 0x8 + i*4 ← arg[i] for each argument dword
#        c. Write mailbox-base + 0x0 ← command ID
#        d. Poll mailbox-base + 0x4 until non-busy (typical timeout 100 ms)
#        e. Read mailbox-base + 0x8 for the response dword(s)
#   3. Error translation:  response 0x01 = OK, 0xFF = unsupported cmd,
#      0xFE = invalid arg, 0xFD = rejected, else = silicon-specific error
#   4. Per-core affinity for Curve Optimizer — SMU commands are global but
#      the encoded argument carries the core index
#
# ZenStates-Core (C#) and PBO2Tuner have the reference implementations and
# match the Vermeer / Raphael command IDs populated above.  For AlienCore
# the WinRing0 IOCTL codes are the same ones already used by the LHM
# bridge, so sharing the driver handle is the cleanest path.
#
# Until this lands, the stub returns a clear message so the AI tool can
# report the unsupported state honestly rather than silently "succeeding".
def _smu_write(command_id: int, args: list[int]) -> tuple[bool, str]:
    return False, ("AMD SMU mailbox backend not yet implemented in "
                   "AlienCore.  Scaffolding is complete — add the WinRing0 "
                   "PCI indirect-access code in "
                   "core/amd_voltage.py::_smu_write() to enable Curve "
                   "Optimizer and PBO writes on AMD systems.")
