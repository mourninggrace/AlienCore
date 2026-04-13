"""
AlienCore - cpu_topology.py
Detects P-core / E-core topology for Intel hybrid CPUs.
Uses GetLogicalProcessorInformationEx via ctypes.
Falls back to a WMI-based heuristic if the API is unavailable.
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
RelationAll            = 0xFFFF

EFFICIENCY_CLASS_PERFORMANCE = 1
EFFICIENCY_CLASS_EFFICIENCY  = 0

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
    Return cached P/E-core topology dict.
    Keys: p_cores, e_cores, p_count, e_count, total_logical, detected
    """
    global _topology
    with _lock:
        if _topology is None:
            _topology = _build_topology()
        return dict(_topology)


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
        "detected": False,   # True = confirmed by OS API, False = estimated
    }
    try:
        import psutil
        result["total_logical"] = psutil.cpu_count(logical=True) or 0
    except Exception:
        pass

    # ── Try GetLogicalProcessorInformationEx ──
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
