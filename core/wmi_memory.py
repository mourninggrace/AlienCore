"""
AlienCore - wmi_memory.py
Reads memory composition from Win32_PerfFormattedData_PerfOS_Memory.

Provides:
  - In Use      (physical RAM committed to processes + kernel)
  - Modified    (dirty pages waiting to be written to disk)
  - Standby     (cached, can be reclaimed instantly)
  - Free        (truly free, zeroed pages)
  - Total       (sum of all above)
  - Bandwidth estimate  (theoretical % of DDR5 peak in use)

Also provides per-process working set data via psutil.
"""

import logging
import psutil
import threading
import time

from core.constants import DDR5_PEAK_GBPS

logger = logging.getLogger("aliencore.wmi_memory")

_last_query_time  = 0.0
_cached_result    = {}
_CACHE_SECS       = 3.0   # don't hammer WMI faster than once per 3 seconds


def get_composition() -> dict:
    """
    Return memory composition in GB.
    {
      in_use_gb, modified_gb, standby_gb, free_gb, total_gb,
      in_use_pct, standby_pct, free_pct,
      bandwidth_pct_estimate,   # rough estimate based on memory pressure
    }
    """
    global _last_query_time, _cached_result
    now = time.monotonic()
    if now - _last_query_time < _CACHE_SECS and _cached_result:
        return dict(_cached_result)

    result = {}
    wmi_raw = [None]

    def _query_wmi():
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass
        try:
            import wmi
            c = wmi.WMI()
            for mem in c.Win32_PerfFormattedData_PerfOS_Memory():
                wmi_raw[0] = {
                    "available":  int(mem.AvailableBytes or 0),
                    "standby":    int(mem.StandbyCacheNormalPriorityBytes or 0) +
                                  int(mem.StandbyCacheReserveBytes or 0) +
                                  int(mem.StandbyCacheCoreBytes or 0),
                    "modified":   int(mem.ModifiedPageListBytes or 0),
                    "free_bytes": int(mem.FreeAndZeroPageListBytes or 0),
                }
                break
        except Exception as e:
            logger.debug("WMI memory query error: %s", e)

    t = threading.Thread(target=_query_wmi, daemon=True)
    t.start()
    t.join(timeout=3.0)

    if wmi_raw[0]:
        try:
            raw     = wmi_raw[0]
            vm      = psutil.virtual_memory()
            total_b = vm.total

            in_use_b  = max(0, total_b - raw["available"])
            standby_b = min(raw["standby"], raw["available"])
            free_b    = max(0, raw["free_bytes"])

            def to_gb(b): return round(b / 1024**3, 2)

            total_gb    = to_gb(total_b)
            in_use_gb   = to_gb(in_use_b)
            modified_gb = to_gb(raw["modified"])
            standby_gb  = to_gb(standby_b)
            free_gb     = to_gb(free_b)

            in_use_pct  = round(in_use_b  / total_b * 100, 1) if total_b else 0
            standby_pct = round(standby_b / total_b * 100, 1) if total_b else 0
            free_pct    = round(free_b    / total_b * 100, 1) if total_b else 0

            bandwidth_est = round(in_use_pct * 0.3, 1)

            result = {
                "in_use_gb":    in_use_gb,
                "modified_gb":  modified_gb,
                "standby_gb":   standby_gb,
                "free_gb":      free_gb,
                "total_gb":     total_gb,
                "in_use_pct":   in_use_pct,
                "standby_pct":  standby_pct,
                "free_pct":     free_pct,
                "bandwidth_pct_estimate": bandwidth_est,
            }
        except Exception as e:
            logger.debug("WMI memory parse error: %s", e)

    if not result:
        logger.debug("WMI memory unavailable or timed out — using psutil fallback")
        try:
            vm = psutil.virtual_memory()
            total_gb  = round(vm.total     / 1024**3, 2)
            used_gb   = round(vm.used      / 1024**3, 2)
            avail_gb  = round(vm.available / 1024**3, 2)
            result = {
                "in_use_gb":    used_gb,
                "modified_gb":  0.0,
                "standby_gb":   avail_gb * 0.5,
                "free_gb":      avail_gb * 0.5,
                "total_gb":     total_gb,
                "in_use_pct":   vm.percent,
                "standby_pct":  round(avail_gb / total_gb * 50, 1) if total_gb else 0,
                "free_pct":     round(avail_gb / total_gb * 50, 1) if total_gb else 0,
                "bandwidth_pct_estimate": round(vm.percent * 0.3, 1),
            }
        except Exception:
            result = {
                "in_use_gb": 0, "modified_gb": 0, "standby_gb": 0,
                "free_gb": 0, "total_gb": 0,
                "in_use_pct": 0, "standby_pct": 0, "free_pct": 0,
                "bandwidth_pct_estimate": 0,
            }

    _cached_result    = result
    _last_query_time  = now
    return dict(result)


def get_top_processes(limit: int = 20) -> list:
    """
    Return top processes by RSS (resident set size), sorted descending.
    [{pid, name, rss_mb, rss_pct}]
    """
    procs = []
    total_ram = psutil.virtual_memory().total
    try:
        for proc in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                mi  = proc.info["memory_info"]
                rss = mi.rss if mi else 0
                procs.append({
                    "pid":     proc.info["pid"],
                    "name":    proc.info["name"] or "?",
                    "rss_mb":  round(rss / 1024**2, 1),
                    "rss_pct": round(rss / total_ram * 100, 2) if total_ram else 0,
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception as e:
        logger.debug("Process list error: %s", e)
    procs.sort(key=lambda p: p["rss_mb"], reverse=True)
    return procs[:limit]


def trim_working_sets(min_rss_mb: float = 50.0) -> dict:
    """
    Call EmptyWorkingSet on all accessible idle processes.
    Returns {trimmed: int, freed_mb_estimate: float, errors: int}
    """
    import ctypes
    trimmed = 0
    errors  = 0
    freed   = 0.0
    kernel32 = ctypes.windll.kernel32

    PROCESS_SET_QUOTA             = 0x0100
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            mi  = proc.info.get("memory_info")
            rss = (mi.rss if mi else 0) / 1024**2
            if rss < min_rss_mb:
                continue
            pid = proc.info["pid"]
            handle = kernel32.OpenProcess(
                PROCESS_SET_QUOTA | PROCESS_QUERY_LIMITED_INFORMATION,
                False, pid
            )
            if not handle:
                errors += 1
                continue
            try:
                before = rss
                kernel32.SetProcessWorkingSetSize(handle, -1, -1)
                trimmed += 1
                # Estimate freed (actual is measured by re-querying, but we skip for speed)
                freed += before * 0.3  # rough: trimming typically reclaims 20-40%
            finally:
                kernel32.CloseHandle(handle)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        except Exception:
            errors += 1

    return {"trimmed": trimmed, "freed_mb_estimate": round(freed, 1), "errors": errors}
