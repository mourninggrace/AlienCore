"""
AlienCore - cpu_topology.py
Detects CPU topology via GetLogicalProcessorInformationEx (Windows API).

Reports:
  * P-core / E-core split for Intel hybrid CPUs (Raptor Lake, Meteor Lake)
  * CCD (Core Complex Die) grouping for AMD Ryzen — Zen 3 and Zen 4 place
    one L3 cache per CCD, so enumerating RelationCache level-3 entries gives
    us the CCD boundaries.  A 5950X reports 2 CCDs of 8 cores each; a 5900X
    reports 2 CCDs of 6 cores each.  Intel CPUs have a single unified L3
    and therefore report ccd_count=1 (or 0 — we don't surface CCD info for
    non-AMD systems).

Falls back to a WMI-based heuristic if the API call fails entirely.
"""

import ctypes
import ctypes.wintypes as wt
import logging
import threading

logger = logging.getLogger("aliencore.cpu_topology")

_lock     = threading.Lock()
_topology = None   # cached result — built once per session


# ─────────────────────────────────────────────────────────────────────────────
# ctypes structures for GetLogicalProcessorInformationEx
# ─────────────────────────────────────────────────────────────────────────────

RelationProcessorCore  = 0
RelationCache          = 2
RelationAll            = 0xFFFF

EFFICIENCY_CLASS_PERFORMANCE = 1
EFFICIENCY_CLASS_EFFICIENCY  = 0

CACHE_LEVEL_L3 = 3

class _PROCESSOR_CORE(ctypes.Structure):
    _fields_ = [("Flags", ctypes.c_ubyte)]

class _PROCESSOR_RELATIONSHIP(ctypes.Structure):
    _fields_ = [
        ("Flags",           ctypes.c_ubyte),
        ("EfficiencyClass", ctypes.c_ubyte),
        ("Reserved",        ctypes.c_ubyte * 20),
        ("GroupCount",      ctypes.c_ushort),
    ]

class _GROUP_AFFINITY(ctypes.Structure):
    # Mask = ULONG_PTR: 8 bytes on 64-bit Windows (c_ulong is 4 bytes on Windows)
    _fields_ = [
        ("Mask",     ctypes.c_ulong if ctypes.sizeof(ctypes.c_ulong) == 8 else ctypes.c_ulonglong),
        ("Group",    ctypes.c_ushort),
        ("Reserved", ctypes.c_ushort * 3),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_topology() -> dict:
    """
    Return cached topology dict.
    Keys:
      p_cores, e_cores, p_count, e_count, total_logical, detected
      ccds (list of {ccd_id, logical_cores, core_count}),
      ccd_count, has_multi_ccd
    """
    global _topology
    with _lock:
        if _topology is None:
            _topology = _build_topology()
        return dict(_topology)


def get_ccds() -> list:
    """Shortcut returning just the CCD grouping list (empty on non-AMD)."""
    return get_topology().get("ccds", [])


def has_multi_ccd() -> bool:
    return get_topology().get("has_multi_ccd", False)


def refresh() -> dict:
    """Force a re-detection (useful after hardware changes)."""
    global _topology
    with _lock:
        _topology = _build_topology()
        return dict(_topology)


# ─────────────────────────────────────────────────────────────────────────────
# Internal
# ─────────────────────────────────────────────────────────────────────────────

def _build_topology() -> dict:
    result = {
        "p_cores": [],
        "e_cores": [],
        "p_count": 0,
        "e_count": 0,
        "total_logical": 0,
        "detected":       False,   # True = confirmed by OS API, False = estimated
        "ccds":           [],      # AMD CCD groups, empty on non-AMD / unknown
        "ccd_count":      0,
        "has_multi_ccd":  False,
    }
    try:
        import psutil
        result["total_logical"] = psutil.cpu_count(logical=True) or 0
    except Exception:
        pass

    # ── CCD detection (AMD Ryzen) — only meaningful on multi-die CPUs ──
    try:
        from core import hardware
        cpu_info = (hardware.get_cached() or {}).get("cpu", {}) or {}
        is_amd   = bool(cpu_info.get("is_amd"))
    except Exception:
        is_amd = False

    if is_amd:
        try:
            ccds = _query_l3_groups()
            if ccds:
                result["ccds"]          = ccds
                result["ccd_count"]     = len(ccds)
                result["has_multi_ccd"] = len(ccds) > 1
                logger.info("AMD topology: %d CCD(s) — %s",
                            len(ccds),
                            ", ".join(f"CCD{c['ccd_id']}={c['core_count']}c"
                                      for c in ccds))
        except Exception as e:
            logger.debug("CCD detection failed: %s", e)

    # ── AMD: no hybrid architecture — all cores are performance cores ──
    # AMD Ryzen CPUs report EfficiencyClass=0 for every core (no E-core concept).
    # _query_glpix would wrongly assign all 32 LPs to e_cores; skip it for AMD
    # and treat all logical processors as P-cores instead.
    if is_amd:
        total = result["total_logical"]
        if total > 0:
            result["p_cores"]  = list(range(total))
            result["e_cores"]  = []
            result["p_count"]  = total
            result["e_count"]  = 0
            result["detected"] = True
            logger.info("AMD topology: %d logical processors (all performance cores)", total)
        return result

    # ── Try GetLogicalProcessorInformationEx for P/E cores (Intel hybrid) ──
    try:
        p_cores, e_cores = _query_glpix()
        if p_cores or e_cores:
            result["p_cores"]  = sorted(p_cores)
            result["e_cores"]  = sorted(e_cores)
            result["p_count"]  = len(p_cores)
            result["e_count"]  = len(e_cores)
            result["detected"] = True
            logger.info("CPU topology (API): %d P-cores, %d E-cores",
                        result["p_count"], result["e_count"])
            return result
    except Exception as e:
        logger.debug("GLPIX topology detection failed: %s", e)

    # ── Fallback: estimate from known Raptor Lake layout ──
    total = result["total_logical"]
    if total > 0:
        # i9-14900HX: 8P (HT) = 16 logical, 16E = 16 logical = 32 total
        # i7-14700HX: 6P (HT) = 12 logical, 20E = 20 logical = 32 total
        # Conservative: split roughly 50/50 for P vs E logical processors
        p_half = total // 2
        result["p_cores"]  = list(range(0, p_half))
        result["e_cores"]  = list(range(p_half, total))
        result["p_count"]  = p_half
        result["e_count"]  = total - p_half
        result["detected"] = False
        logger.info("CPU topology (estimated): %d P-logical, %d E-logical",
                    result["p_count"], result["e_count"])
    return result


def _query_l3_groups() -> list:
    """
    Enumerate RelationCache entries at Level 3 and return one entry per L3
    region.  On Zen 3 / Zen 4 each CCD has its own dedicated L3, so the L3
    count equals the CCD count and each L3's affinity mask identifies which
    logical cores live on that CCD.

    Returns a list of dicts:
      [{"ccd_id": int, "logical_cores": [...], "core_count": int}, ...]
    sorted by lowest logical-core index.
    """
    kernel32 = ctypes.windll.kernel32
    ERROR_INSUFFICIENT_BUFFER = 122

    size = wt.DWORD(0)
    kernel32.GetLogicalProcessorInformationEx(
        RelationCache, None, ctypes.byref(size))
    err = kernel32.GetLastError()
    if err != ERROR_INSUFFICIENT_BUFFER or size.value == 0:
        raise OSError(f"GLPIX(cache) size query failed (err={err})")

    buf = (ctypes.c_byte * size.value)()
    if not kernel32.GetLogicalProcessorInformationEx(
            RelationCache, ctypes.cast(buf, ctypes.c_void_p),
            ctypes.byref(size)):
        raise OSError("GLPIX(cache) data query failed")

    groups  = []
    offset  = 0
    buf_len = size.value

    while offset < buf_len:
        rel_type = ctypes.c_uint32.from_buffer_copy(buf, offset).value
        rec_size = ctypes.c_uint32.from_buffer_copy(buf, offset + 4).value

        if rel_type == RelationCache:
            # CACHE_RELATIONSHIP starts at offset +8:
            #   +8   Level         (BYTE)
            #   +9   Associativity (BYTE)
            #   +10  LineSize      (WORD)
            #   +12  CacheSize     (DWORD)
            #   +16  Type          (DWORD enum)
            #   +20  Reserved[20]  — 20 bytes
            #   +40  GroupCount    (WORD)
            # GroupMask[] follows at +48 (8-byte aligned for ULONG_PTR).
            level       = ctypes.c_ubyte .from_buffer_copy(buf, offset + 8 ).value
            group_count = ctypes.c_uint16.from_buffer_copy(buf, offset + 40).value

            if level == CACHE_LEVEL_L3:
                ga_offset = offset + 48
                ga_size   = ctypes.sizeof(_GROUP_AFFINITY)
                cores     = []
                for g in range(group_count):
                    ga   = _GROUP_AFFINITY.from_buffer_copy(
                        buf, ga_offset + g * ga_size)
                    mask = ga.Mask
                    bit  = 0
                    while mask:
                        if mask & 1:
                            cores.append(ga.Group * 64 + bit)
                        mask >>= 1
                        bit  += 1
                if cores:
                    groups.append(sorted(cores))

        offset += rec_size

    groups.sort(key=lambda c: c[0] if c else 0)
    return [
        {"ccd_id": i, "logical_cores": cores, "core_count": len(cores)}
        for i, cores in enumerate(groups)
    ]


def _query_glpix():
    """
    Call GetLogicalProcessorInformationEx(RelationProcessorCore).
    Returns (p_cores_set, e_cores_set) where each entry is a logical processor index.
    """
    kernel32   = ctypes.windll.kernel32
    ERROR_INSUFFICIENT_BUFFER = 122

    # First call: get required buffer size
    size = wt.DWORD(0)
    kernel32.GetLogicalProcessorInformationEx(RelationProcessorCore, None, ctypes.byref(size))
    err = kernel32.GetLastError()
    if err != ERROR_INSUFFICIENT_BUFFER or size.value == 0:
        raise OSError(f"GLPIX size query failed (err={err})")

    buf = (ctypes.c_byte * size.value)()
    if not kernel32.GetLogicalProcessorInformationEx(
            RelationProcessorCore, ctypes.cast(buf, ctypes.c_void_p),
            ctypes.byref(size)):
        raise OSError("GLPIX data query failed")

    p_cores = []
    e_cores = []
    offset  = 0
    buf_len = size.value

    while offset < buf_len:
        # Relationship type (DWORD at offset 0)
        rel_type = ctypes.c_uint32.from_buffer_copy(buf, offset).value
        # Size (DWORD at offset 4)
        rec_size = ctypes.c_uint32.from_buffer_copy(buf, offset + 4).value

        if rel_type == RelationProcessorCore:
            # EfficiencyClass is at byte offset 9 in SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX
            #   RelationShip(4) + Size(4) + union{ ProcessorCore { Flags(1), EfficiencyClass(1) } }
            eff_class = ctypes.c_ubyte.from_buffer_copy(buf, offset + 9).value

            # GroupCount is at offset 28 (after the full PROCESSOR_RELATIONSHIP struct)
            # Structure layout:
            #   +0  Relationship LOGICAL_PROCESSOR_RELATIONSHIP (DWORD)
            #   +4  Size (DWORD)
            #   +8  ProcessorRelationship { Flags(1), EfficiencyClass(1), Reserved(20), GroupCount(2) }
            # So GroupCount is at +8+1+1+20 = +30
            group_count = ctypes.c_uint16.from_buffer_copy(buf, offset + 30).value
            # GroupAffinity array starts at offset 32
            ga_offset = offset + 32
            ga_size   = ctypes.sizeof(_GROUP_AFFINITY)

            for g in range(group_count):
                ga = _GROUP_AFFINITY.from_buffer_copy(buf, ga_offset + g * ga_size)
                mask = ga.Mask
                bit  = 0
                while mask:
                    if mask & 1:
                        logical_idx = g * 64 + bit
                        if eff_class >= EFFICIENCY_CLASS_PERFORMANCE:
                            p_cores.append(logical_idx)
                        else:
                            e_cores.append(logical_idx)
                    mask >>= 1
                    bit  += 1

        offset += rec_size

    return p_cores, e_cores
