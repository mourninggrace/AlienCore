"""
AlienCore - throttle_log.py
Records GPU thermal throttle events parsed from nvidia-smi.

nvidia-smi reports clocks_throttle_reasons.active as a hex bitmask.
We decode it into human-readable reasons and log transitions.
"""

import json
import logging
import os
import time
import threading

from core.constants import THROTTLE_LOG_PATH

logger = logging.getLogger("aliencore.throttle_log")

_lock         = threading.Lock()
_recent       = []    # in-memory ring buffer of recent events (last 200)
_last_reasons = set()  # reasons from the previous poll — used to detect transitions

# nvidia-smi throttle reason bitmask definitions
_REASON_MAP = {
    0x0000000000000001: "Idle (GPU idle)",
    0x0000000000000002: "Application clock setting",
    0x0000000000000004: "SW power cap",
    0x0000000000000008: "HW slowdown",
    0x0000000000000010: "Sync boost (multi-GPU)",
    0x0000000000000020: "SW thermal slowdown",
    0x0000000000000040: "HW thermal slowdown",
    0x0000000000000080: "HW power brake slowdown",
    0x0000000000000200: "Frame buffer bandwidth limit",
    0x0000000000002000: "Low utilization",
}

# Reasons that indicate actual performance loss (exclude idle and low-util)
_PERF_LIMITING = {0x4, 0x8, 0x20, 0x40, 0x80, 0x200}


def decode_reasons(hex_str: str) -> list[str]:
    """
    Convert a hex bitmask string (e.g. '0x0000000000000040') to a list of reason strings.
    Returns an empty list if no throttle is active or the value is invalid.
    """
    try:
        mask = int(hex_str, 16)
    except (ValueError, TypeError):
        return []
    if mask == 0:
        return []
    reasons = []
    for bit, label in _REASON_MAP.items():
        if mask & bit:
            reasons.append(label)
    return reasons


def is_perf_limited(hex_str: str) -> bool:
    """Return True if the bitmask contains any performance-limiting reasons."""
    try:
        mask = int(hex_str, 16)
    except (ValueError, TypeError):
        return False
    return any(mask & bit for bit in _PERF_LIMITING)


def record(hex_str: str, gpu_temp: float | None = None, gpu_watts: float | None = None):
    """
    Record a throttle event if the active reasons have changed from the previous poll.
    Call this every sensor cycle from sensors.py.
    """
    global _last_reasons, _recent

    reasons = set(decode_reasons(hex_str))
    perf_limited = is_perf_limited(hex_str)

    # Only log when throttle state changes
    if reasons == _last_reasons:
        return

    prev_reasons   = _last_reasons
    _last_reasons  = reasons

    # Only store events that actually mean performance was lost
    if not perf_limited and not prev_reasons:
        return

    event = {
        "timestamp":    time.time(),
        "reasons":      sorted(reasons),
        "was":          sorted(prev_reasons),
        "perf_limited": perf_limited,
        "gpu_temp":     gpu_temp,
        "gpu_watts":    gpu_watts,
    }

    with _lock:
        _recent.append(event)
        if len(_recent) > 200:
            _recent = _recent[-200:]

    if perf_limited:
        logger.info("GPU throttle: %s (%.0f°C, %.0fW)",
                    ", ".join(reasons),
                    gpu_temp or 0,
                    gpu_watts or 0)

    _persist(event)


def get_recent(limit: int = 50) -> list:
    """Return the most recent throttle events (newest last)."""
    with _lock:
        return list(_recent[-limit:])


def get_active() -> list:
    """Return the currently active throttle reasons (from last poll)."""
    with _lock:
        return sorted(_last_reasons)


def clear():
    """Clear in-memory and on-disk log."""
    global _recent, _last_reasons
    with _lock:
        _recent       = []
        _last_reasons = set()
    try:
        if os.path.exists(THROTTLE_LOG_PATH):
            os.remove(THROTTLE_LOG_PATH)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Persistence
# ─────────────────────────────────────────────────────────────────────────────

def _persist(event: dict):
    """Append one event to the on-disk log atomically."""
    try:
        os.makedirs(os.path.dirname(THROTTLE_LOG_PATH), exist_ok=True)
        existing = []
        if os.path.exists(THROTTLE_LOG_PATH):
            try:
                with open(THROTTLE_LOG_PATH, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if not isinstance(existing, list):
                    existing = []
            except Exception:
                existing = []
        existing.append(event)
        existing = existing[-500:]  # keep last 500 events on disk
        tmp = THROTTLE_LOG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(existing, f)
        os.replace(tmp, THROTTLE_LOG_PATH)
    except Exception as e:
        logger.debug("Throttle log persist error: %s", e)


def load_history() -> list:
    """Load persisted throttle events from disk."""
    try:
        if os.path.exists(THROTTLE_LOG_PATH):
            with open(THROTTLE_LOG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []
