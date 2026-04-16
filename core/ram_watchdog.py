"""
AlienCore - ram_watchdog.py
Memory leak watchdog — monitors per-process RSS over time and flags
processes whose memory growth rate exceeds a configurable threshold.

Runs as part of the monitor loop (not a standalone thread).
Call update() every monitor cycle. Call get_suspects() to see flagged processes.
"""

import logging
import time
import psutil
from collections import defaultdict, deque

logger = logging.getLogger("aliencore.ram_watchdog")

# {pid: deque of (timestamp, rss_bytes)} — rolling window per process
# maxlen=900 → 30 min at 2s poll intervals (covers the maximum configurable window)
_history: dict[int, deque] = defaultdict(lambda: deque(maxlen=900))
# {pid: {"name", "pid", "growth_mb_per_min", "current_mb", "started_at"}}
_suspects: dict[int, dict] = {}

# Minimum seconds between real process_iter scans.  process_iter across ~300
# Windows processes with memory_info is ~40–80 ms — too expensive to run on
# every 3 s monitor tick.  Growth rates are measured in MB/min, so 15 s
# resolution is more than enough.
_MIN_SCAN_INTERVAL = 15.0
_last_scan_at      = 0.0


def update(window_minutes: float = 5.0, threshold_mb_per_min: float = 50.0):
    """
    Sample all process RSS values and update growth rate estimates.
    Flag any process exceeding threshold_mb_per_min sustained over window_minutes.
    Call this periodically (e.g., every SENSOR_POLL_INTERVAL seconds) — this
    function internally rate-limits to at most once per _MIN_SCAN_INTERVAL.
    """
    global _suspects, _last_scan_at
    now = time.time()
    if now - _last_scan_at < _MIN_SCAN_INTERVAL:
        return
    _last_scan_at = now

    window_secs  = window_minutes * 60.0
    new_suspects = {}
    live_pids: set[int] = set()

    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            mi  = proc.info.get("memory_info")
            if mi is None:
                continue
            pid  = proc.info["pid"]
            name = proc.info["name"] or "?"
            rss  = mi.rss
            live_pids.add(pid)

            dq = _history[pid]
            dq.append((now, rss))

            # Need at least 2 samples spanning window_secs to compute growth
            if len(dq) < 2:
                continue
            oldest = dq[0]
            dt_secs = now - oldest[0]
            if dt_secs < max(30.0, window_secs * 0.5):
                continue   # not enough history yet

            delta_bytes = rss - oldest[1]
            if delta_bytes <= 0:
                continue   # shrinking or stable

            growth_mb_per_min = (delta_bytes / 1024**2) / (dt_secs / 60.0)

            if growth_mb_per_min >= threshold_mb_per_min:
                new_suspects[pid] = {
                    "pid":              pid,
                    "name":             name,
                    "growth_mb_per_min": round(growth_mb_per_min, 1),
                    "current_mb":       round(rss / 1024**2, 1),
                    "started_at":       oldest[0],
                }

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        except Exception:
            pass

    # Purge history for dead processes (live_pids collected during the scan
    # loop above — saves a second full process_iter call per update).
    dead = [pid for pid in _history if pid not in live_pids]
    for pid in dead:
        del _history[pid]
        new_suspects.pop(pid, None)

    _suspects = new_suspects

    if _suspects:
        names = [v["name"] for v in _suspects.values()]
        logger.debug("Memory leak suspects: %s", ", ".join(names))


def get_suspects() -> list:
    """Return current list of suspected leaking processes (sorted by growth rate)."""
    return sorted(_suspects.values(), key=lambda x: x["growth_mb_per_min"], reverse=True)


def clear():
    """Reset all history and suspects (e.g., after a reboot or manual clear)."""
    global _suspects
    _history.clear()
    _suspects = {}
