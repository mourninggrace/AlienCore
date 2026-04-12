"""
Tests for core/config_manager.py
Covers: _deep_merge, load/save/reload, get_value, set_value.
All I/O is redirected to tmp_path — no real config.json is touched.
"""

import json
import os
import sys
import pytest

# Ensure the project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─────────────────────────────────────────────────────────────────────────────
# _deep_merge
# ─────────────────────────────────────────────────────────────────────────────

def test_deep_merge_basic():
    from core.config_manager import _deep_merge
    base     = {"a": 1, "b": {"c": 2, "d": 3}}
    override = {"b": {"c": 99}, "e": 5}
    result   = _deep_merge(base, override)
    assert result == {"a": 1, "b": {"c": 99, "d": 3}, "e": 5}


def test_deep_merge_preserves_new_base_keys():
    from core.config_manager import _deep_merge
    base     = {"a": 1, "new_key": True}
    override = {"a": 2}
    result   = _deep_merge(base, override)
    assert result["new_key"] is True
    assert result["a"] == 2


def test_deep_merge_non_dict_override_replaces():
    from core.config_manager import _deep_merge
    base     = {"a": {"nested": 1}}
    override = {"a": "replaced"}
    result   = _deep_merge(base, override)
    assert result["a"] == "replaced"


def test_deep_merge_does_not_mutate_base():
    from core.config_manager import _deep_merge
    base     = {"a": {"b": 1}}
    override = {"a": {"b": 99}}
    _deep_merge(base, override)
    assert base["a"]["b"] == 1   # original unchanged


def test_deep_merge_empty_override():
    from core.config_manager import _deep_merge
    base   = {"a": 1, "b": 2}
    result = _deep_merge(base, {})
    assert result == {"a": 1, "b": 2}


def test_deep_merge_empty_base():
    from core.config_manager import _deep_merge
    result = _deep_merge({}, {"x": 10})
    assert result == {"x": 10}


# ─────────────────────────────────────────────────────────────────────────────
# load / save / get_value / set_value  (patched to tmp_path)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def patch_config_path(tmp_path, monkeypatch):
    """Redirect all config I/O to a temp directory for every test in this file."""
    cfg_path = str(tmp_path / "config.json")
    import core.config_manager as cm
    monkeypatch.setattr(cm, "CONFIG_PATH", cfg_path)
    # Also patch _write_locked to use the new path
    original_write = cm._write_locked
    def patched_write(cfg_dict):
        os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
        tmp = cfg_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg_dict, f, indent=2)
        os.replace(tmp, cfg_path)
    monkeypatch.setattr(cm, "_write_locked", patched_write)
    yield cfg_path


def test_load_creates_defaults_when_missing(tmp_path):
    import core.config_manager as cm
    result = cm.load()
    assert "cpu" in result
    assert "thresholds" in result
    assert os.path.exists(str(tmp_path / "config.json"))


def test_load_merges_with_defaults(tmp_path):
    import core.config_manager as cm
    cfg_path = str(tmp_path / "config.json")
    # Write a partial config that's missing newer keys
    partial = {"cpu": {"dynamic_throttle": False}}
    with open(cfg_path, "w") as f:
        json.dump(partial, f)
    result = cm.load()
    # New default keys present
    assert "thresholds" in result
    # User value preserved
    assert result["cpu"]["dynamic_throttle"] is False


def test_save_and_reload(tmp_path):
    import core.config_manager as cm
    cm.load()
    c = cm.get()
    c["cpu"]["dynamic_throttle"] = False
    cm.save(c)
    cm.reload()
    assert cm.get()["cpu"]["dynamic_throttle"] is False


def test_get_value_nested(tmp_path):
    import core.config_manager as cm
    cm.load()
    val = cm.get_value("thresholds", "cpu_warn")
    assert isinstance(val, int)
    assert 50 <= val <= 100


def test_get_value_missing_returns_default(tmp_path):
    import core.config_manager as cm
    cm.load()
    val = cm.get_value("nonexistent", "key", default="fallback")
    assert val == "fallback"


def test_set_value_persists(tmp_path):
    import core.config_manager as cm
    cm.load()
    cm.set_value("thresholds", "cpu_warn", value=72)
    cm.reload()
    assert cm.get_value("thresholds", "cpu_warn") == 72


def test_is_first_run_true_by_default(tmp_path):
    import core.config_manager as cm
    cm.load()
    assert cm.is_first_run() is True


def test_mark_first_run_complete(tmp_path):
    import core.config_manager as cm
    cm.load()
    cm.mark_first_run_complete()
    assert cm.is_first_run() is False
