"""
AlienCore - memory_tuning.py
Read-only memory status + AMD-side runtime memory-subsystem tuning.

What this module does and does NOT do, and why:

NOT IMPLEMENTED (and will not be) — DDR frequency / primary-timing changes
at runtime.  The integrated memory controller trains against DIMMs at
POST using SPD + XMP (Intel) / EXPO (AMD DDR5) data.  Changing frequency
or primary timings (CL, tRCD, tRP, tRAS) while the OS is running will
desync the memory controller state machine and crash the machine.  Tools
that claim to "overclock RAM from within Windows" either do nothing,
change only Windows memory-manager policy (which is not the same thing),
or rely on motherboard-vendor SMI flashing which is not exposed to
user-mode applications.  If you need tighter DIMM timings, enter BIOS
and configure XMP/EXPO or manual timings — there is no userspace path.

WHAT WE CAN DO AT RUNTIME:

  1. READ STATUS — report XMP/EXPO state, DIMM frequency, primary
     timings, and memory-controller voltage.  Purely informational,
     safe on any platform.

  2. AMD INFINITY FABRIC CLOCK (FCLK) — on Ryzen, FCLK is the clock
     that couples the memory controller to the Core Complex Die(s).
     Higher FCLK (up to ~1:1 with MCLK, typically 1800-2000 MHz)
     reduces memory latency.  Writable via the SMU mailbox — same
     path as amd_voltage's Curve Optimizer, different command ID.

  3. AMD MEMORY CONTROLLER / SOC VOLTAGE — VDDG_IOD, VDDG_CCD,
     VDD_SOC all sit behind SMU commands.  Useful when pushing FCLK
     above stock, but carries the same "too-low causes hang, too-high
     degrades silicon over time" risk surface as Curve Optimizer.

The FCLK and SoC-voltage paths are scaffolded the same way as
amd_voltage.set_curve_offset — full validation, range clamping, AI
tool surface, but the _smu_write() call at the bottom is currently
stubbed.  Implementation reuses core/amd_voltage.py::_smu_write() once
that lands, since both go through the same SMU mailbox.

DEPENDENCIES:
    WinRing0.sys driver (already loaded by the LHM bridge).
    Read-only status works without any driver — pulls from WMI and
    from core/hardware.py's cached RAM info.
"""
# copykitten

import logging

logger = logging.getLogger("aliencore.memory_tuning")

# ─── Conservative FCLK range (MHz) ──────────────────────────────────────────
# Zen 3 sweet spot is 1800-2000 MHz (1:1 with DDR4-3600 to 4000).  Going
# below 1333 or above 2100 is unusual and rarely productive.
FCLK_MIN_MHZ = 1333
FCLK_MAX_MHZ = 2100

# ─── Conservative SoC / memory-controller voltage ranges (mV) ───────────────
# Exceeding these accelerates silicon degradation on Zen 3 / Zen 4.
SOC_VOLTAGE_MIN_MV = 900
SOC_VOLTAGE_MAX_MV = 1150   # Zen 3 silicon-damage risk above 1.15 V
VDDG_MIN_MV        = 850
VDDG_MAX_MV        = 1050

# ─── AMD SMU command IDs for memory-subsystem tuning ─────────────────────────
# Command IDs per Zen generation — populated from ZenStates-Core / ZenTimings
# source.  Will dispatch through amd_voltage._smu_write() once implemented.
_SMU_MEMORY = {
    "zen3": {"set_fclk_mhz": 0x47, "set_soc_mv": 0x48, "set_vddg_mv": 0x49},
    "zen4": {"set_fclk_mhz": 0x49, "set_soc_mv": 0x4A, "set_vddg_mv": 0x4B},
    "zen2": {"set_fclk_mhz": 0x3E, "set_soc_mv": 0x3F, "set_vddg_mv": None},
    "zen5": {"set_fclk_mhz": 0x49, "set_soc_mv": 0x4A, "set_vddg_mv": 0x4B},
}


# ─────────────────────────────────────────────────────────────────────────────
# Status
# ─────────────────────────────────────────────────────────────────────────────

def _ram_cache() -> dict:
    try:
        from core import hardware
        hw  = hardware.get_cached() or {}
        ram = hw.get("ram", {}) or {}
        cpu = hw.get("cpu", {}) or {}
    except Exception:
        ram, cpu = {}, {}
    return {"ram": ram, "cpu": cpu}


def _estimate_xmp_expo(ram: dict) -> dict:
    """Infer XMP/EXPO state from the configured DIMM frequency vs JEDEC defaults.

    SPD exposes only JEDEC-spec fallback speeds (DDR4 up to 3200, DDR5 up to
    5600).  If Windows sees a faster configured frequency than any JEDEC
    value, an XMP (Intel) or EXPO (AMD DDR5) profile must be active.
    Without reading the SPD side-band, this is the best we can do from
    user-mode.
    """
    speeds = [s.get("speed_mhz", 0) for s in (ram.get("slots") or [])]
    speed  = max(speeds) if speeds else 0
    # DDR4 JEDEC max = 3200 MT/s, DDR5 JEDEC max = 5600 MT/s (as of 2025).
    ddr5_hint = speed >= 4800
    jedec_cap = 5600 if ddr5_hint else 3200
    return {
        "configured_mts":      speed,
        "likely_ddr5":         ddr5_hint,
        "xmp_expo_active":     speed > jedec_cap if speed else None,
        "jedec_cap_assumed":   jedec_cap,
    }


def get_status() -> dict:
    info = _ram_cache()
    ram  = info["ram"]
    cpu  = info["cpu"]

    base = {
        "total_gb":        ram.get("total_gb", 0),
        "slot_count":      len(ram.get("slots") or []),
        "slots":           ram.get("slots") or [],
    }
    base.update(_estimate_xmp_expo(ram))

    amd_tuning = {
        "supported":     False,
        "reason":        "",
        "zen_gen":       None,
        "fclk_range":    [FCLK_MIN_MHZ,       FCLK_MAX_MHZ],
        "soc_mv_range":  [SOC_VOLTAGE_MIN_MV, SOC_VOLTAGE_MAX_MV],
        "vddg_mv_range": [VDDG_MIN_MV,        VDDG_MAX_MV],
    }

    if not cpu.get("is_amd"):
        amd_tuning["reason"] = "AMD memory tuning requires an AMD Ryzen CPU."
    else:
        try:
            from core import amd_voltage
            av_status = amd_voltage.get_status()
            zen_gen = av_status.get("zen_gen")
        except Exception as e:
            zen_gen = None
            amd_tuning["reason"] = f"amd_voltage dispatch failed: {e}"
        if zen_gen and zen_gen in _SMU_MEMORY:
            amd_tuning["supported"] = True
            amd_tuning["zen_gen"]   = zen_gen
        elif not amd_tuning["reason"]:
            amd_tuning["reason"] = (f"Unrecognized Zen generation for memory "
                                    f"tuning (zen_gen={zen_gen}).")

    base["amd_memory_tuning"] = amd_tuning
    return base


def status_text() -> str:
    s = get_status()
    parts = [f"RAM: {s['total_gb']} GB across {s['slot_count']} slot(s)."]
    if s.get("configured_mts"):
        parts.append(f"Configured speed: {s['configured_mts']} MT/s.")
        if s.get("xmp_expo_active") is True:
            parts.append("XMP/EXPO profile is ACTIVE.")
        elif s.get("xmp_expo_active") is False:
            parts.append(f"At or below JEDEC cap ({s['jedec_cap_assumed']} MT/s) — "
                         "XMP/EXPO likely NOT active.")
    amd = s["amd_memory_tuning"]
    if amd["supported"]:
        parts.append(f"AMD memory tuning available on {amd['zen_gen']}: "
                     f"FCLK {FCLK_MIN_MHZ}..{FCLK_MAX_MHZ} MHz, "
                     f"SoC voltage {SOC_VOLTAGE_MIN_MV}..{SOC_VOLTAGE_MAX_MV} mV "
                     "(scaffolded, SMU write backend not yet implemented).")
    elif amd["reason"]:
        parts.append(f"AMD memory tuning: {amd['reason']}")
    parts.append("Runtime DDR frequency / primary timing changes are not "
                 "exposed — use BIOS XMP/EXPO for those.")
    return " ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# AMD FCLK + SoC voltage — scaffolded, SMU write stubbed via amd_voltage
# ─────────────────────────────────────────────────────────────────────────────

def _smu_write_shim(cmd_id: int, args: list) -> tuple[bool, str]:
    """Route through amd_voltage._smu_write once implemented."""
    try:
        from core import amd_voltage
        return amd_voltage._smu_write(cmd_id, args)
    except Exception as e:
        return False, f"SMU dispatch failed: {e}"


def set_amd_fclk(fclk_mhz: int) -> tuple[bool, str]:
    s = get_status()["amd_memory_tuning"]
    if not s["supported"]:
        return False, s["reason"] or "AMD memory tuning unsupported."
    try:
        fclk_mhz = int(fclk_mhz)
    except (TypeError, ValueError):
        return False, f"fclk_mhz must be an integer (got {fclk_mhz!r})."
    if not (FCLK_MIN_MHZ <= fclk_mhz <= FCLK_MAX_MHZ):
        return False, (f"FCLK {fclk_mhz} MHz out of range "
                       f"({FCLK_MIN_MHZ}..{FCLK_MAX_MHZ}).")
    cmds = _SMU_MEMORY[s["zen_gen"]]
    ok, msg = _smu_write_shim(cmds["set_fclk_mhz"], [fclk_mhz])
    if not ok:
        return False, msg
    logger.info("AMD FCLK set to %d MHz", fclk_mhz)
    return True, f"Set Infinity Fabric clock to {fclk_mhz} MHz."


def set_amd_soc_voltage(millivolts: int) -> tuple[bool, str]:
    s = get_status()["amd_memory_tuning"]
    if not s["supported"]:
        return False, s["reason"] or "AMD memory tuning unsupported."
    try:
        millivolts = int(millivolts)
    except (TypeError, ValueError):
        return False, f"millivolts must be an integer (got {millivolts!r})."
    if not (SOC_VOLTAGE_MIN_MV <= millivolts <= SOC_VOLTAGE_MAX_MV):
        return False, (f"SoC voltage {millivolts} mV out of range "
                       f"({SOC_VOLTAGE_MIN_MV}..{SOC_VOLTAGE_MAX_MV}).")
    cmds = _SMU_MEMORY[s["zen_gen"]]
    ok, msg = _smu_write_shim(cmds["set_soc_mv"], [millivolts])
    if not ok:
        return False, msg
    logger.info("AMD SoC voltage set to %d mV", millivolts)
    return True, f"Set VDD_SOC to {millivolts} mV."


def set_amd_vddg(millivolts: int, plane: str = "ccd") -> tuple[bool, str]:
    """Set VDDG_CCD or VDDG_IOD (Zen 3+ only)."""
    s = get_status()["amd_memory_tuning"]
    if not s["supported"]:
        return False, s["reason"] or "AMD memory tuning unsupported."
    cmds = _SMU_MEMORY[s["zen_gen"]]
    cmd_id = cmds.get("set_vddg_mv")
    if cmd_id is None:
        return False, f"VDDG control not available on {s['zen_gen']} (Zen 3 or newer required)."
    plane = (plane or "ccd").lower()
    if plane not in ("ccd", "iod"):
        return False, f"Unknown VDDG plane '{plane}'. Use 'ccd' or 'iod'."
    try:
        millivolts = int(millivolts)
    except (TypeError, ValueError):
        return False, f"millivolts must be an integer (got {millivolts!r})."
    if not (VDDG_MIN_MV <= millivolts <= VDDG_MAX_MV):
        return False, (f"VDDG {millivolts} mV out of range "
                       f"({VDDG_MIN_MV}..{VDDG_MAX_MV}).")
    arg = (1 if plane == "iod" else 0) << 24 | (millivolts & 0xFFFF)
    ok, msg = _smu_write_shim(cmd_id, [arg])
    if not ok:
        return False, msg
    logger.info("AMD VDDG_%s set to %d mV", plane.upper(), millivolts)
    return True, f"Set VDDG_{plane.upper()} to {millivolts} mV."
