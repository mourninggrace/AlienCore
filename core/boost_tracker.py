"""
AlienCore - boost_tracker.py
Tracks CPU boost clock sustainability and TVB headroom.

Maintains a rolling window of CPU frequency samples to compute:
  - Boost sustainability score (0–100%)  — how often the CPU is at/above 90% of max
  - TVB headroom indicator               — CPU temp vs. TVB threshold
  - Boost history log for trend analysis
"""

import json
import logging
import os
import threading
import time

from core.constants import (
    BOOST_HISTORY_PATH,
    TVB_TEMP_THRESHOLD,
)

logger = logging.getLogger("aliencore.boost_tracker")

_lock          = threading.Lock()
_samples       = []          # list of (timestamp, freq_ghz, temp_c)
_WINDOW_SECS   = 60          # rolling window for score calculation
_MAX_STORED    = 500         # cap on in-memory samples

# Populated once from hardware profile
_max_freq_mhz  = 0
_tvb_threshold = TVB_TEMP_THRESHOLD


def configure(max_freq_mhz: int, tvb_threshold: float = TVB_TEMP_THRESHOLD):
    """Set the hardware-specific parameters (called at startup)."""
    global _max_freq_mhz, _tvb_threshold
    _max_freq_mhz  = max_freq_mhz
    _tvb_threshold = tvb_threshold


def record(freq_ghz: float, temp_c: float):
    """Record one sample (called from the sensor poll loop)."""
    global _samples
    now = time.time()
    with _lock:
        _samples.append((now, freq_ghz, temp_c))
        # Trim old samples
        cutoff = now - _WINDOW_SECS
        _samples = [s for s in _samples if s[0] >= cutoff]
        if len(_samples) > _MAX_STORED:
            _samples = _samples[-_MAX_STORED:]


def get_score() -> dict:
    """
    Return current boost stats.
    {
      score_pct: float     — 0-100, how often boost is active in the rolling window
      avg_freq_ghz: float  — average CPU freq over the window
      max_freq_mhz: int    — known CPU max freq
      tvb_headroom_c: float| None  — degrees below TVB threshold (positive = TVB active)
      tvb_active: bool
      window_seconds: int
      sample_count: int
    }
    """
    with _lock:
        samples = list(_samples)

    if not samples or _max_freq_mhz <= 0:
        return {
            "score_pct":      0.0,
            "avg_freq_ghz":   0.0,
            "max_freq_mhz":   _max_freq_mhz,
            "tvb_headroom_c": None,
            "tvb_active":     False,
            "window_seconds": _WINDOW_SECS,
            "sample_count":   len(samples),
        }

    boost_threshold_ghz = (_max_freq_mhz * 0.90) / 1000.0
    freqs = [s[1] for s in samples if s[1] is not None]
    temps = [s[2] for s in samples if s[2] is not None]

    at_boost = sum(1 for f in freqs if f >= boost_threshold_ghz)
    score    = round(at_boost / len(freqs) * 100, 1) if freqs else 0.0
    avg_freq = round(sum(freqs) / len(freqs), 2) if freqs else 0.0

    latest_temp = temps[-1] if temps else None
    tvb_headroom = round(_tvb_threshold - latest_temp, 1) if latest_temp is not None else None
    tvb_active   = (latest_temp is not None and latest_temp < _tvb_threshold)

    return {
        "score_pct":      score,
        "avg_freq_ghz":   avg_freq,
        "max_freq_mhz":   _max_freq_mhz,
        "tvb_headroom_c": tvb_headroom,
        "tvb_active":     tvb_active,
        "window_seconds": _WINDOW_SECS,
        "sample_count":   len(samples),
    }


def save_history():
    """Persist the current session snapshot to disk (called periodically or on shutdown)."""
    try:
        os.makedirs(os.path.dirname(BOOST_HISTORY_PATH), exist_ok=True)
        with _lock:
            samples = list(_samples)
        entry = {
            "timestamp":    time.time(),
            "score":        get_score(),
            "sample_count": len(samples),
        }
        # Read existing, append, keep last 200 entries
        existing = []
        if os.path.exists(BOOST_HISTORY_PATH):
            try:
                with open(BOOST_HISTORY_PATH, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if not isinstance(existing, list):
                    existing = []
            except Exception:
                existing = []
        existing.append(entry)
        existing = existing[-200:]
        tmp = BOOST_HISTORY_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(existing, f)
        os.replace(tmp, BOOST_HISTORY_PATH)
    except Exception as e:
        logger.debug("Boost history save error: %s", e)


def load_history() -> list:
    """Return the persisted history list (for display in the settings UI)."""
    try:
        if os.path.exists(BOOST_HISTORY_PATH):
            with open(BOOST_HISTORY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []
