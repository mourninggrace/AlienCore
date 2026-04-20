"""
Tests for pure-logic functions in core/sensors.py
Covers: fmt_temp (celsius/fahrenheit), color_for_temp.

cfg.get() is monkeypatched — no real hardware needed.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def patch_cfg_celsius(monkeypatch):
    import core.config_manager as cm
    from core.constants import DEFAULT_CONFIG
    import copy
    monkeypatch.setattr(cm, "_config", copy.deepcopy(DEFAULT_CONFIG))
    yield


# ─────────────────────────────────────────────────────────────────────────────
# fmt_temp
# ─────────────────────────────────────────────────────────────────────────────

def test_fmt_temp_celsius_integer():
    from core.sensors import fmt_temp
    assert fmt_temp(72.0) == "72°"


def test_fmt_temp_celsius_truncates_not_rounds():
    from core.sensors import fmt_temp
    # int() truncates toward zero
    assert fmt_temp(72.9) == "72°"


def test_fmt_temp_fahrenheit(monkeypatch):
    import core.config_manager as cm
    from core.constants import DEFAULT_CONFIG
    import copy
    c = copy.deepcopy(DEFAULT_CONFIG)
    c["display"]["temp_unit"] = "fahrenheit"
    monkeypatch.setattr(cm, "_config", c)
    monkeypatch.setattr(cm, "_cache", {})
    monkeypatch.setattr(cm, "_cache_valid", False)
    from core.sensors import fmt_temp
    # 0°C = 32°F
    assert fmt_temp(0.0) == "32°"
    # 100°C = 212°F
    assert fmt_temp(100.0) == "212°"
    # 72°C = 161.6°F → 161°
    assert fmt_temp(72.0) == "161°"


def test_fmt_temp_zero_celsius():
    from core.sensors import fmt_temp
    assert fmt_temp(0.0) == "0°"


def test_fmt_temp_negative():
    from core.sensors import fmt_temp
    assert fmt_temp(-5.0) == "-5°"


# ─────────────────────────────────────────────────────────────────────────────
# color_for_temp
# ─────────────────────────────────────────────────────────────────────────────

def test_color_cool_below_warn():
    from core.sensors import color_for_temp
    from core.constants import COLOR_COOL
    assert color_for_temp(70.0, warn=80, crit=95) == COLOR_COOL


def test_color_warm_at_warn():
    from core.sensors import color_for_temp
    from core.constants import COLOR_WARM
    assert color_for_temp(80.0, warn=80, crit=95) == COLOR_WARM


def test_color_warm_between_warn_and_crit():
    from core.sensors import color_for_temp
    from core.constants import COLOR_WARM
    assert color_for_temp(87.0, warn=80, crit=95) == COLOR_WARM


def test_color_hot_at_crit():
    from core.sensors import color_for_temp
    from core.constants import COLOR_HOT
    assert color_for_temp(95.0, warn=80, crit=95) == COLOR_HOT


def test_color_hot_above_crit():
    from core.sensors import color_for_temp
    from core.constants import COLOR_HOT
    assert color_for_temp(100.0, warn=80, crit=95) == COLOR_HOT


def test_color_none_temp_returns_cool():
    from core.sensors import color_for_temp
    from core.constants import COLOR_COOL
    assert color_for_temp(None, warn=80, crit=95) == COLOR_COOL
