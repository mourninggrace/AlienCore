"""
Tests for core/ai_advisor.py
Covers: parse_response validation, type coercion, range checking,
        str-enum validation, user_profile behavior keys, _cfg_get/_cfg_set,
        and Proposal creation.

No API calls are made — AI responses are supplied as raw strings.
cfg.get() is monkeypatched to return controlled config dicts.
"""

import json
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_response(assessment: str, changes: list) -> str:
    return json.dumps({"assessment": assessment, "changes": changes})


def _base_config() -> dict:
    """Minimal config snapshot sufficient for advisor tests."""
    from core.constants import DEFAULT_CONFIG
    import copy
    return copy.deepcopy(DEFAULT_CONFIG)


@pytest.fixture(autouse=True)
def patch_cfg(monkeypatch):
    """Patch cfg.get() to return a clean copy of DEFAULT_CONFIG for every test."""
    import core.config_manager as cm
    from core.constants import DEFAULT_CONFIG
    import copy
    monkeypatch.setattr(cm, "_config", copy.deepcopy(DEFAULT_CONFIG))
    # cm.get() returns a cached snapshot and reassigns _cache on rebuild.
    # Patch both _cache (so monkeypatch captures+restores the pre-test
    # reference) and _cache_valid (so the next get() rebuilds from our
    # patched _config). Without this, a test leaves the module cache
    # pointing at its patched config and poisons subsequent tests.
    monkeypatch.setattr(cm, "_cache", {})
    monkeypatch.setattr(cm, "_cache_valid", False)
    yield


# ─────────────────────────────────────────────────────────────────────────────
# parse_response — basic functionality
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_valid_int_proposal():
    from core.ai_advisor import parse_response
    raw = _make_response("All good.", [
        {"key": "thresholds.cpu_warn", "proposed": 75, "reason": "runs hot"}
    ])
    assessment, proposals = parse_response(raw)
    assert assessment == "All good."
    assert len(proposals) == 1
    p = proposals[0]
    assert p.key == "thresholds.cpu_warn"
    assert p.proposed == 75
    assert p.reason == "runs hot"


def test_parse_valid_bool_proposal():
    from core.ai_advisor import parse_response
    raw = _make_response("Tweak suggested.", [
        {"key": "cpu.dynamic_throttle", "proposed": False, "reason": "gaming session"}
    ])
    _, proposals = parse_response(raw)
    assert len(proposals) == 1
    assert proposals[0].proposed is False


def test_parse_valid_float_proposal():
    from core.ai_advisor import parse_response
    raw = _make_response("Minor tweak.", [
        {"key": "display.update_interval_value", "proposed": 3.0, "reason": "reduce cpu overhead"}
    ])
    _, proposals = parse_response(raw)
    assert len(proposals) == 1
    assert proposals[0].proposed == pytest.approx(3.0)


def test_parse_empty_changes():
    from core.ai_advisor import parse_response
    raw = _make_response("System looks healthy.", [])
    assessment, proposals = parse_response(raw)
    assert assessment == "System looks healthy."
    assert proposals == []


def test_parse_markdown_fences_stripped():
    from core.ai_advisor import parse_response
    raw = '```json\n' + _make_response("OK", []) + '\n```'
    assessment, proposals = parse_response(raw)
    assert assessment == "OK"


def test_parse_non_json_returns_empty():
    from core.ai_advisor import parse_response
    assessment, proposals = parse_response("Sorry, I cannot help with that.")
    assert proposals == []


# ─────────────────────────────────────────────────────────────────────────────
# parse_response — validation / filtering
# ─────────────────────────────────────────────────────────────────────────────

def test_unknown_key_is_dropped():
    from core.ai_advisor import parse_response
    raw = _make_response("Suggestion.", [
        {"key": "nonexistent.made_up_key", "proposed": 42, "reason": "hallucinated"}
    ])
    _, proposals = parse_response(raw)
    assert proposals == []


def test_out_of_range_int_is_dropped():
    from core.ai_advisor import parse_response
    # cpu_warn range is 50–95; 200 is out of range
    raw = _make_response("Hot.", [
        {"key": "thresholds.cpu_warn", "proposed": 200, "reason": "too high"}
    ])
    _, proposals = parse_response(raw)
    assert proposals == []


def test_below_range_int_is_dropped():
    from core.ai_advisor import parse_response
    # cpu_warn range is 50–95; 10 is below range
    raw = _make_response("Cool.", [
        {"key": "thresholds.cpu_warn", "proposed": 10, "reason": "too low"}
    ])
    _, proposals = parse_response(raw)
    assert proposals == []


def test_already_current_value_is_dropped():
    from core.ai_advisor import parse_response
    from core import config_manager as cm
    # Default cpu_warn is 80; proposing 80 should be dropped
    current = cm.get()["thresholds"]["cpu_warn"]
    raw = _make_response("No change needed.", [
        {"key": "thresholds.cpu_warn", "proposed": current, "reason": "same value"}
    ])
    _, proposals = parse_response(raw)
    assert proposals == []


def test_bool_coerced_from_string():
    from core.ai_advisor import parse_response
    # AI may return "true" as a string instead of JSON true
    raw = _make_response("Tweak.", [
        {"key": "cpu.hetero_scheduling", "proposed": "false", "reason": "testing"}
    ])
    _, proposals = parse_response(raw)
    # hetero_scheduling defaults True; "false" string → False bool
    assert len(proposals) == 1
    assert proposals[0].proposed is False


def test_invalid_type_is_dropped():
    from core.ai_advisor import parse_response
    raw = _make_response("Bad.", [
        {"key": "thresholds.cpu_warn", "proposed": "not_a_number", "reason": "error"}
    ])
    _, proposals = parse_response(raw)
    assert proposals == []


# ─────────────────────────────────────────────────────────────────────────────
# parse_response — str enum type
# ─────────────────────────────────────────────────────────────────────────────

def test_str_enum_valid_value():
    from core.ai_advisor import parse_response
    from core import config_manager as cm
    # Default temp_unit is "celsius"; propose "fahrenheit"
    raw = _make_response("Unit change.", [
        {"key": "display.temp_unit", "proposed": "fahrenheit", "reason": "preference"}
    ])
    _, proposals = parse_response(raw)
    assert len(proposals) == 1
    assert proposals[0].proposed == "fahrenheit"


def test_str_enum_invalid_value_is_dropped():
    from core.ai_advisor import parse_response
    raw = _make_response("Bad unit.", [
        {"key": "display.temp_unit", "proposed": "kelvin", "reason": "invalid"}
    ])
    _, proposals = parse_response(raw)
    assert proposals == []


def test_str_enum_case_normalized():
    from core.ai_advisor import parse_response
    # AI might return "Fahrenheit" with a capital
    raw = _make_response("Unit.", [
        {"key": "display.temp_unit", "proposed": "Fahrenheit", "reason": "caps"}
    ])
    _, proposals = parse_response(raw)
    assert len(proposals) == 1
    assert proposals[0].proposed == "fahrenheit"


# ─────────────────────────────────────────────────────────────────────────────
# User profile behavior keys
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def config_with_user_profile(monkeypatch):
    """Patch cfg to include one user-defined profile named 'editing'."""
    import core.config_manager as cm
    from core.constants import DEFAULT_CONFIG
    import copy
    c = copy.deepcopy(DEFAULT_CONFIG)
    c["profiles"]["user_profiles"] = [{
        "name":     "editing",
        "label":    "Video Editing",
        "processes": ["premiere.exe"],
        "behavior": "streaming",
        "color":    "#aa00ff",
        "priority": 50,
    }]
    monkeypatch.setattr(cm, "_config", c)
    monkeypatch.setattr(cm, "_cache", {})
    monkeypatch.setattr(cm, "_cache_valid", False)
    yield c


def test_dynamic_tunables_includes_user_profile(config_with_user_profile):
    from core.ai_advisor import _dynamic_tunables
    dt = _dynamic_tunables()
    assert "user_profile:editing.behavior" in dt
    label, typ, allowed, _ = dt["user_profile:editing.behavior"]
    assert "Video Editing" in label
    assert typ is str
    assert "gaming" in allowed
    assert "streaming" in allowed


def test_parse_user_profile_behavior_valid(config_with_user_profile):
    from core.ai_advisor import parse_response
    # Current behavior is "streaming"; propose "gaming"
    raw = _make_response("Profile tweak.", [
        {"key": "user_profile:editing.behavior", "proposed": "gaming",
         "reason": "GPU-heavy editing needs gaming profile behavior"}
    ])
    _, proposals = parse_response(raw)
    assert len(proposals) == 1
    assert proposals[0].proposed == "gaming"
    assert proposals[0].key == "user_profile:editing.behavior"


def test_parse_user_profile_behavior_invalid_value(config_with_user_profile):
    from core.ai_advisor import parse_response
    raw = _make_response("Bad.", [
        {"key": "user_profile:editing.behavior", "proposed": "turbo",
         "reason": "not a valid behavior"}
    ])
    _, proposals = parse_response(raw)
    assert proposals == []


def test_parse_user_profile_behavior_same_value_dropped(config_with_user_profile):
    from core.ai_advisor import parse_response
    # Current behavior is "streaming"; proposing same should be dropped
    raw = _make_response("No change.", [
        {"key": "user_profile:editing.behavior", "proposed": "streaming",
         "reason": "already set"}
    ])
    _, proposals = parse_response(raw)
    assert proposals == []


# ─────────────────────────────────────────────────────────────────────────────
# _cfg_get / _cfg_set for user_profile: keys
# ─────────────────────────────────────────────────────────────────────────────

def test_cfg_get_user_profile_behavior(config_with_user_profile):
    from core.ai_advisor import _cfg_get
    val = _cfg_get("user_profile:editing.behavior")
    assert val == "streaming"


def test_cfg_get_user_profile_missing_returns_none():
    from core.ai_advisor import _cfg_get
    val = _cfg_get("user_profile:nonexistent.behavior")
    assert val is None


def test_cfg_get_flat_key():
    from core.ai_advisor import _cfg_get
    val = _cfg_get("thresholds.cpu_warn")
    assert isinstance(val, int)


# ─────────────────────────────────────────────────────────────────────────────
# Proposal label
# ─────────────────────────────────────────────────────────────────────────────

def test_proposal_has_label():
    from core.ai_advisor import parse_response
    raw = _make_response("Label check.", [
        {"key": "thresholds.cpu_warn", "proposed": 78, "reason": "test"}
    ])
    _, proposals = parse_response(raw)
    assert len(proposals) == 1
    assert proposals[0].label == "CPU warn temp (°C)"


def test_proposal_user_profile_has_label(config_with_user_profile):
    from core.ai_advisor import parse_response
    raw = _make_response("Label check.", [
        {"key": "user_profile:editing.behavior", "proposed": "idle", "reason": "quiet"}
    ])
    _, proposals = parse_response(raw)
    assert len(proposals) == 1
    assert "Video Editing" in proposals[0].label


# ─────────────────────────────────────────────────────────────────────────────
# _all_tunables completeness
# ─────────────────────────────────────────────────────────────────────────────

def test_all_tunables_includes_new_keys():
    from core.ai_advisor import _all_tunables
    at = _all_tunables()
    new_keys = [
        "network.disable_rsc",
        "network.disable_ecn",
        "network.tcp_socket_tuning",
        "network.qos_reserve_fix",
        "network.flush_dns_on_switch",
        "awcc.gmode_on_gaming",
        "awcc.sync_profile",
        "visual.disable_animations",
        "visual.disable_transparency",
        "display.auto_hide_fullscreen",
        "display.temp_unit",
        "profiles.detect_by_load",
        "turbo_cool.auto_off_on_cool",
        "service.notify_on_profile_switch",
    ]
    for key in new_keys:
        assert key in at, f"Expected key missing from TUNABLE: {key}"
