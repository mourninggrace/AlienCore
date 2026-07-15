"""
Tests for the hardware-safety clamps added during the algorithm review.

These assert that every value the engine can *apply* stays inside a
hardware-safe range even when fed extreme / adversarial input:
  - processor-state percentage  (tweaks._clamp_proc_state)
  - GPU power-limit write        (gpu_tuning.set_power_limit)
  - dynamic CPU ceiling          (monitor._adjust_cpu_ceiling_dynamically)
  - boost-tracker sample intake  (boost_tracker.record / get_score)

They are deliberately pure (no powercfg / NVML calls reach hardware): the
clamp helpers are exercised directly, and the higher-level functions are
driven with monkeypatched write paths so the test asserts on the *value*
that would have been written.
"""

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─────────────────────────────────────────────────────────────────────────────
# tweaks._clamp_proc_state  — the single choke point for every powercfg
# processor-state write in the app.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    (50, 50),
    (0, 0),
    (100, 100),
    (-20, 0),        # negative ceiling is nonsense → floor at 0
    (250, 100),      # over-100 → cap at 100
    (5.4, 5),        # rounds
    (99.6, 100),
    (10**9, 100),    # absurd overflow-ish value still bounded
])
def test_clamp_proc_state_in_range(raw, expected):
    from core import tweaks
    assert tweaks._clamp_proc_state(raw) == expected


@pytest.mark.parametrize("bad", [None, "garbage", object()])
def test_clamp_proc_state_invalid_defaults_safe(bad):
    from core import tweaks
    # Garbage input must default to the *non-restricting* 100% (never an
    # arbitrary low ceiling that could throttle a machine on bad config).
    assert tweaks._clamp_proc_state(bad) == 100


def test_clamp_proc_state_nan_defaults_safe():
    from core import tweaks
    assert tweaks._clamp_proc_state(float("nan")) == 100


def test_clamp_proc_state_inf_bounded():
    from core import tweaks
    assert tweaks._clamp_proc_state(float("inf")) == 100
    assert tweaks._clamp_proc_state(float("-inf")) == 0


def test_set_max_processor_state_never_writes_out_of_range(monkeypatch):
    """An out-of-range pct must reach powercfg only after clamping."""
    from core import tweaks
    written = []

    monkeypatch.setattr(tweaks, "_get_active_scheme", lambda: "SCHEME-GUID")

    def fake_run(args, dry_run, description=""):
        # capture the value index write (the numeric pct is the last arg)
        if any("valueindex" in str(a) for a in args):
            written.append(int(args[-1]))

    monkeypatch.setattr(tweaks, "_run", fake_run)

    for adversarial in (-100, 500, float("nan"), None):
        tweaks._set_max_processor_state(adversarial, "AC", dry_run=False)

    assert written, "expected at least one powercfg write"
    assert all(0 <= v <= 100 for v in written), written


# ─────────────────────────────────────────────────────────────────────────────
# gpu_tuning.set_power_limit — must refuse to write when it cannot bound-check.
# ─────────────────────────────────────────────────────────────────────────────

def test_power_limit_refuses_when_constraints_unknown(monkeypatch):
    from core import gpu_tuning
    monkeypatch.setattr(gpu_tuning, "_primary_gpu_info",
                        lambda: {"vendor": "nvidia", "name": "Test"})
    # NVML couldn't report firmware constraints → both None.
    monkeypatch.setattr(gpu_tuning, "_nvidia_status", lambda: {
        "power_limit_min_w": None, "power_limit_max_w": None})

    called = {"wrote": False}
    def fake_call(fn, *a):
        called["wrote"] = True
        return True, "OK"
    monkeypatch.setattr(gpu_tuning, "_nvml_call", fake_call)

    ok, msg = gpu_tuning.set_power_limit(400)
    assert ok is False
    assert called["wrote"] is False, "must not write an unbounded power limit"


def test_power_limit_rejects_above_firmware_max(monkeypatch):
    from core import gpu_tuning
    monkeypatch.setattr(gpu_tuning, "_primary_gpu_info",
                        lambda: {"vendor": "nvidia", "name": "Test"})
    monkeypatch.setattr(gpu_tuning, "_nvidia_status", lambda: {
        "power_limit_min_w": 100.0, "power_limit_max_w": 175.0})

    called = {"wrote": False}
    monkeypatch.setattr(gpu_tuning, "_nvml_call",
                        lambda fn, *a: (called.__setitem__("wrote", True), (True, "OK"))[1])

    ok, msg = gpu_tuning.set_power_limit(300)   # well above 175 W max
    assert ok is False
    assert called["wrote"] is False


def test_power_limit_accepts_in_range(monkeypatch):
    from core import gpu_tuning
    monkeypatch.setattr(gpu_tuning, "_primary_gpu_info",
                        lambda: {"vendor": "nvidia", "name": "Test"})
    monkeypatch.setattr(gpu_tuning, "_nvidia_status", lambda: {
        "power_limit_min_w": 100.0, "power_limit_max_w": 175.0})
    seen = {}
    def fake_call(fn, *a):
        seen["milliwatts"] = a[0]
        return True, "OK"
    monkeypatch.setattr(gpu_tuning, "_nvml_call", fake_call)

    ok, msg = gpu_tuning.set_power_limit(150)
    assert ok is True
    # 150 W → 150000 mW (unit conversion sanity)
    assert seen["milliwatts"] == 150000


# ─────────────────────────────────────────────────────────────────────────────
# monitor._adjust_cpu_ceiling_dynamically — must not raise the ceiling on a
# non-finite temperature sample.
# ─────────────────────────────────────────────────────────────────────────────

def _ceiling_config():
    return {"cpu": {"throttle_temp_trigger": 75, "idle_max_state_pct": 40}}


def test_ceiling_holds_on_nan_temp(monkeypatch):
    from core import monitor
    writes = []
    monkeypatch.setattr(monitor.tweaks, "_set_max_processor_state",
                        lambda pct, ac, dry_run: writes.append(pct))

    monitor._current_cpu_ceiling = 40
    monitor._pending_cpu_ceiling = None
    monitor._pending_since = None

    readings = {"cpu_temp_avg": float("nan"), "cpu_load_pct": 50}
    monitor._adjust_cpu_ceiling_dynamically(readings, _ceiling_config())

    # NaN temp + busy load previously fell through to target=100; now it must
    # make NO write at all (hold current ceiling).
    assert writes == [], f"NaN temp must not drive a ceiling change, got {writes}"


def test_ceiling_holds_on_inf_load(monkeypatch):
    from core import monitor
    writes = []
    monkeypatch.setattr(monitor.tweaks, "_set_max_processor_state",
                        lambda pct, ac, dry_run: writes.append(pct))
    monitor._current_cpu_ceiling = 40
    monitor._pending_cpu_ceiling = None
    monitor._pending_since = None

    readings = {"cpu_temp_avg": 60, "cpu_load_pct": float("inf")}
    monitor._adjust_cpu_ceiling_dynamically(readings, _ceiling_config())
    assert writes == []


# ─────────────────────────────────────────────────────────────────────────────
# boost_tracker.record — must drop NaN/inf/out-of-range samples so they can't
# poison the rolling-average boost score.
# ─────────────────────────────────────────────────────────────────────────────

def test_boost_tracker_drops_nan_sample():
    from core import boost_tracker as bt
    bt._samples = []
    bt.configure(max_freq_mhz=5000)

    bt.record(float("nan"), float("nan"))
    bt.record(4.8, 60.0)           # one good sample
    bt.record(float("inf"), 9999)  # absurd

    score = bt.get_score()
    # avg_freq must be finite and equal to the single valid sample.
    assert math.isfinite(score["avg_freq_ghz"])
    assert score["avg_freq_ghz"] == pytest.approx(4.8, abs=0.01)
    # score_pct must be a finite 0..100 number.
    assert math.isfinite(score["score_pct"])
    assert 0.0 <= score["score_pct"] <= 100.0


def test_boost_tracker_all_bad_samples_safe():
    from core import boost_tracker as bt
    bt._samples = []
    bt.configure(max_freq_mhz=5000)
    bt.record(float("nan"), float("nan"))
    bt.record(-5, -10)
    bt.record(99.0, 500.0)   # 99 GHz / 500C — physically impossible
    score = bt.get_score()
    assert math.isfinite(score["avg_freq_ghz"])
    assert 0.0 <= score["score_pct"] <= 100.0
