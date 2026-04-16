"""
AlienCore - boost_tracker.py
Tracks CPU boost clock sustainability and TVB headroom.

Maintains a rolling window of CPU samples and computes a single
"boost sustainability" score (0–100%) using one of three formulas selected
by the user in Settings → CPU:

  frequency    — % of window with CPU at ≥90% of max clock.
                 Measures boost realization. 100% = CPU is holding turbo.
  thermal      — % of window with CPU temp below the TVB threshold.
                 Measures thermal headroom. 100% = thermals are NOT the bottleneck.
  core_parking — avg fraction of cores actively participating (load > 2%).
                 Measures scheduler behavior. 100% = Windows is using every core;
                 low scores mean the parking policy is holding cores back.
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
# list of (timestamp, freq_ghz, temp_c, cores_list_or_None)
_samples       = []
_WINDOW_SECS   = 60
_MAX_STORED    = 500

# Populated once from hardware profile
_max_freq_mhz  = 0
_tvb_threshold = TVB_TEMP_THRESHOLD

# A core is considered "active" (not parked) if it exceeds this percent.
# 2% filters out background interrupts while catching any real workload.
_PARKED_CORE_THRESHOLD_PCT = 2.0


def configure(max_freq_mhz: int, tvb_threshold: float = TVB_TEMP_THRESHOLD):
    """Set the hardware-specific parameters (called at startup)."""
    global _max_freq_mhz, _tvb_threshold
    _max_freq_mhz  = max_freq_mhz
    _tvb_threshold = tvb_threshold


def record(freq_ghz: float, temp_c: float, cores: list | None = None):
    """Record one sample (called from the sensor poll loop)."""
    global _samples
    now = time.time()
    with _lock:
        _samples.append((now, freq_ghz, temp_c, cores))
        cutoff = now - _WINDOW_SECS
        _samples = [s for s in _samples if s[0] >= cutoff]
        if len(_samples) > _MAX_STORED:
            _samples = _samples[-_MAX_STORED:]


def _get_mode() -> str:
    """Read the current scoring mode from config. Import lazily to avoid cycles."""
    try:
        from core import config_manager as cfg
        mode = cfg.get_value("cpu", "boost_score_mode", default="frequency")
        if mode in ("frequency", "thermal", "core_parking"):
            return mode
    except Exception:
        pass
    return "frequency"


def get_score() -> dict:
    """
    Return current boost stats.
    {
      score_pct: float        — 0-100, computed per the active scoring mode
      score_mode: str         — "frequency" | "thermal" | "core_parking"
      avg_freq_ghz: float     — average CPU freq over the window
      max_freq_mhz: int       — known CPU max freq
      tvb_headroom_c: float|None
      tvb_active: bool
      window_seconds: int
      sample_count: int
    }
    """
    mode = _get_mode()
    with _lock:
        samples = list(_samples)

    empty = {
        "score_pct":      0.0,
        "score_mode":     mode,
        "avg_freq_ghz":   0.0,
        "max_freq_mhz":   _max_freq_mhz,
        "tvb_headroom_c": None,
        "tvb_active":     False,
        "window_seconds": _WINDOW_SECS,
        "sample_count":   len(samples),
    }
    if not samples:
        return empty

    freqs = [s[1] for s in samples if s[1] is not None]
    temps = [s[2] for s in samples if s[2] is not None]
    avg_freq = round(sum(freqs) / len(freqs), 2) if freqs else 0.0

    # Score — formula depends on mode
    if mode == "thermal":
        # % of samples where CPU was cool enough for TVB to stay engaged.
        # High score = thermals are not limiting boost.
        if temps:
            cool = sum(1 for t in temps if t < _tvb_threshold)
            score = round(cool / len(temps) * 100, 1)
        else:
            score = 0.0

    elif mode == "core_parking":
        # Avg fraction of cores with load > _PARKED_CORE_THRESHOLD_PCT.
        # Samples where cpu_load_cores is None or empty are skipped so the
        # score doesn't dilute while the data is warming up.
        ratios = []
        for s in samples:
            cores = s[3]
            if not cores:
                continue
            active = sum(1 for c in cores if c is not None
                         and c > _PARKED_CORE_THRESHOLD_PCT)
            ratios.append(active / len(cores))
        score = round(sum(ratios) / len(ratios) * 100, 1) if ratios else 0.0

    else:  # "frequency"
        if _max_freq_mhz <= 0 or not freqs:
            score = 0.0
        else:
            boost_threshold_ghz = (_max_freq_mhz * 0.90) / 1000.0
            at_boost = sum(1 for f in freqs if f >= boost_threshold_ghz)
            score = round(at_boost / len(freqs) * 100, 1)

    latest_temp  = temps[-1] if temps else None
    tvb_headroom = round(_tvb_threshold - latest_temp, 1) if latest_temp is not None else None
    tvb_active   = (latest_temp is not None and latest_temp < _tvb_threshold)

    return {
        "score_pct":      score,
        "score_mode":     mode,
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
