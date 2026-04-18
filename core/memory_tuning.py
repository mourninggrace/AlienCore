"""
AlienCore - memory_tuning.py
Read-only memory status reporting.

DDR frequency and primary timings are not user-mode tunable on any
platform.  The integrated memory controller trains against DIMMs at
POST using SPD + XMP (Intel) data; changing frequency or CL/tRCD/tRP/
tRAS at runtime desyncs the controller state machine and crashes the
machine.  Tools that claim to "overclock RAM from within Windows"
either do nothing, only change Windows memory-manager policy (not the
same thing), or rely on motherboard-vendor SMI flashing that isn't
exposed to user-mode.  If you need tighter DIMM timings, enter BIOS
and configure XMP or manual timings.

This module reports:
  * total capacity, slot layout, configured DIMM frequency
  * inferred XMP state (heuristic — SPD side-band not readable from
    user-mode)

DEPENDENCIES:
    None beyond core/hardware.py's cached RAM info.
"""
# copykitten

import logging

logger = logging.getLogger("aliencore.memory_tuning")


# ─────────────────────────────────────────────────────────────────────────────
# Status
# ─────────────────────────────────────────────────────────────────────────────

def _ram_cache() -> dict:
    try:
        from core import hardware
        hw  = hardware.get_cached() or {}
        ram = hw.get("ram", {}) or {}
    except Exception:
        ram = {}
    return {"ram": ram}


def _estimate_xmp(ram: dict) -> dict:
    """Infer XMP state from the configured DIMM frequency vs JEDEC defaults.

    SPD exposes only JEDEC-spec fallback speeds (DDR4 up to 3200, DDR5 up to
    5600).  If Windows sees a faster configured frequency than any JEDEC
    value, an XMP profile must be active.  Without reading the SPD side-band,
    this is the best we can do from user-mode.
    """
    speeds = [s.get("speed_mhz", 0) for s in (ram.get("slots") or [])]
    speed  = max(speeds) if speeds else 0
    # DDR4 JEDEC max = 3200 MT/s, DDR5 JEDEC max = 5600 MT/s (as of 2025).
    ddr5_hint = speed >= 4800
    jedec_cap = 5600 if ddr5_hint else 3200
    return {
        "configured_mts":      speed,
        "likely_ddr5":         ddr5_hint,
        "xmp_active":          speed > jedec_cap if speed else None,
        "jedec_cap_assumed":   jedec_cap,
    }


def get_status() -> dict:
    info = _ram_cache()
    ram  = info["ram"]

    base = {
        "total_gb":        ram.get("total_gb", 0),
        "slot_count":      len(ram.get("slots") or []),
        "slots":           ram.get("slots") or [],
    }
    base.update(_estimate_xmp(ram))
    return base


def status_text() -> str:
    s = get_status()
    parts = [f"RAM: {s['total_gb']} GB across {s['slot_count']} slot(s)."]
    if s.get("configured_mts"):
        parts.append(f"Configured speed: {s['configured_mts']} MT/s.")
        if s.get("xmp_active") is True:
            parts.append("XMP profile is ACTIVE.")
        elif s.get("xmp_active") is False:
            parts.append(f"At or below JEDEC cap ({s['jedec_cap_assumed']} MT/s) — "
                         "XMP likely NOT active.")
    parts.append("Runtime DDR frequency / primary timing changes are not "
                 "exposed — use BIOS XMP for those.")
    return " ".join(parts)
