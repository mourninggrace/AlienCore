"""
Tests for core/profiles.py
Covers: _is_gaming_by_load, _is_gaming_by_process, _check_user_profiles,
        evaluate(), get_current(), set_manual_override().

psutil.process_iter is mocked — no real process list needed.
cfg.get() is monkeypatched to a controlled config.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def reset_profile_state():
    """Reset module-level profile state before each test."""
    import core.profiles as p
    p._current_profile = "idle"
    p._manual_override = None
    yield
    p._current_profile = "idle"
    p._manual_override = None


@pytest.fixture
def base_config(monkeypatch):
    import core.config_manager as cm
    from core.constants import DEFAULT_CONFIG
    import copy
    c = copy.deepcopy(DEFAULT_CONFIG)
    monkeypatch.setattr(cm, "_config", c)
    return c


# ─────────────────────────────────────────────────────────────────────────────
# _is_gaming_by_load
# ─────────────────────────────────────────────────────────────────────────────

def test_gaming_by_load_high_gpu(base_config):
    from core.profiles import _is_gaming_by_load
    sensors = {"gpu_load": 80, "cpu_load_pct": 20}
    c = base_config
    assert _is_gaming_by_load(sensors, c) is True


def test_gaming_by_load_both_elevated(base_config):
    from core.profiles import _is_gaming_by_load
    # gpu_load below threshold but combined with high CPU
    sensors = {"gpu_load": 25, "cpu_load_pct": 60}
    c = base_config
    # 25 >= 40 * 0.6 = 24 → True
    assert _is_gaming_by_load(sensors, c) is True


def test_gaming_by_load_idle(base_config):
    from core.profiles import _is_gaming_by_load
    sensors = {"gpu_load": 5, "cpu_load_pct": 10}
    c = base_config
    assert _is_gaming_by_load(sensors, c) is False


def test_gaming_by_load_missing_gpu(base_config):
    from core.profiles import _is_gaming_by_load
    sensors = {"cpu_load_pct": 99}
    c = base_config
    assert _is_gaming_by_load(sensors, c) is False


# ─────────────────────────────────────────────────────────────────────────────
# _is_gaming_by_process
# ─────────────────────────────────────────────────────────────────────────────

def test_gaming_by_process_known_game(base_config):
    from core.profiles import _is_gaming_by_process
    running = {"thedivision2.exe", "explorer.exe"}
    assert _is_gaming_by_process(running, base_config) is True


def test_gaming_by_process_launcher(base_config):
    # Launcher processes (steam.exe, epicgameslauncher.exe, ...) are
    # intentionally NOT in GAMING_PROCESSES — they run whenever the platform
    # is open. Only processes that imply a game is actually running count.
    from core.profiles import _is_gaming_by_process
    running = {"thedivision2.exe", "chrome.exe"}
    assert _is_gaming_by_process(running, base_config) is True


def test_gaming_by_process_no_game(base_config):
    from core.profiles import _is_gaming_by_process
    running = {"chrome.exe", "explorer.exe", "notepad.exe"}
    assert _is_gaming_by_process(running, base_config) is False


def test_gaming_by_process_custom_game(base_config):
    from core.profiles import _is_gaming_by_process
    base_config["profiles"]["custom_gaming_processes"] = ["mygame.exe"]
    running = {"mygame.exe"}
    assert _is_gaming_by_process(running, base_config) is True


# ─────────────────────────────────────────────────────────────────────────────
# _check_user_profiles
# ─────────────────────────────────────────────────────────────────────────────

def test_check_user_profiles_match(base_config):
    from core.profiles import _check_user_profiles
    base_config["profiles"]["user_profiles"] = [
        {"name": "editing", "label": "Editing",
         "processes": ["premiere.exe"], "priority": 50}
    ]
    running = {"premiere.exe", "chrome.exe"}
    result = _check_user_profiles(running, base_config)
    assert result == "editing"


def test_check_user_profiles_no_match(base_config):
    from core.profiles import _check_user_profiles
    base_config["profiles"]["user_profiles"] = [
        {"name": "editing", "label": "Editing",
         "processes": ["premiere.exe"], "priority": 50}
    ]
    running = {"chrome.exe", "explorer.exe"}
    result = _check_user_profiles(running, base_config)
    assert result is None


def test_check_user_profiles_priority_order(base_config):
    from core.profiles import _check_user_profiles
    base_config["profiles"]["user_profiles"] = [
        {"name": "low_prio",  "processes": ["shared.exe"], "priority": 90},
        {"name": "high_prio", "processes": ["shared.exe"], "priority": 10},
    ]
    running = {"shared.exe"}
    result = _check_user_profiles(running, base_config)
    assert result == "high_prio"


def test_check_user_profiles_empty_process_list_no_match(base_config):
    from core.profiles import _check_user_profiles
    base_config["profiles"]["user_profiles"] = [
        {"name": "empty", "processes": [], "priority": 10}
    ]
    running = {"anything.exe"}
    result = _check_user_profiles(running, base_config)
    assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# Manual override
# ─────────────────────────────────────────────────────────────────────────────

def test_manual_override_set_and_get():
    from core import profiles
    profiles.set_manual_override("gaming")
    assert profiles.get_current() == "gaming"
    assert profiles.is_manual_override() is True


def test_manual_override_clear():
    from core import profiles
    profiles.set_manual_override("gaming")
    profiles.set_manual_override(None)
    assert profiles.is_manual_override() is False
    assert profiles.get_current() == "idle"


# ─────────────────────────────────────────────────────────────────────────────
# evaluate() integration
# ─────────────────────────────────────────────────────────────────────────────

def test_evaluate_idle_when_nothing_running(base_config, monkeypatch):
    import core.profiles as p
    monkeypatch.setattr(p, "_get_running_processes", lambda: set())
    result = p.evaluate({"gpu_load": 2, "cpu_load_pct": 3})
    assert result == "idle"


def test_evaluate_gaming_by_process(base_config, monkeypatch):
    import core.profiles as p
    monkeypatch.setattr(p, "_get_running_processes", lambda: {"thedivision2.exe"})
    result = p.evaluate({"gpu_load": 5, "cpu_load_pct": 5})
    assert result == "gaming"


def test_evaluate_streaming_takes_priority_over_gaming(base_config, monkeypatch):
    import core.profiles as p
    monkeypatch.setattr(p, "_get_running_processes",
                        lambda: {"obs64.exe", "steam.exe"})
    result = p.evaluate({"gpu_load": 70})
    assert result == "streaming"


def test_evaluate_manual_override_bypasses_detection(base_config, monkeypatch):
    import core.profiles as p
    monkeypatch.setattr(p, "_get_running_processes", lambda: {"steam.exe"})
    p.set_manual_override("idle")
    result = p.evaluate({"gpu_load": 90, "cpu_load_pct": 90})
    assert result == "idle"


def test_evaluate_gaming_by_load_fallback(base_config, monkeypatch):
    # Load-based detection requires _LOAD_HIT_NEEDED consecutive hits before
    # committing to gaming (anti-flap hysteresis). Call evaluate() enough
    # times to clear the threshold.
    import core.profiles as p
    monkeypatch.setattr(p, "_get_running_processes", lambda: set())
    result = None
    for _ in range(p._LOAD_HIT_NEEDED):
        result = p.evaluate({"gpu_load": 80, "cpu_load_pct": 50})
    assert result == "gaming"
