"""
AlienCore - config_manager.py
Handles loading, saving, validating, and live-reloading config.json.
The service calls reload() after the settings GUI saves changes — no restart needed.
"""

import json
import copy
import logging
import os
from threading import Lock
from core.constants import CONFIG_PATH, DEFAULT_CONFIG

logger = logging.getLogger("aliencore.config")
_lock = Lock()
_config = {}
_cache: dict = {}          # pre-built snapshot returned by get(); rebuilt on writes
_cache_valid: bool = False # False = cache must be rebuilt before next get()


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base without losing keys added in newer versions."""
    result = copy.deepcopy(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load() -> dict:
    """Load config from disk. If missing, write defaults and return them."""
    global _config, _cache_valid
    with _lock:
        if not os.path.exists(CONFIG_PATH):
            logger.info("No config found — writing defaults to %s", CONFIG_PATH)
            _config = copy.deepcopy(DEFAULT_CONFIG)
            _write_locked(_config)
            _cache_valid = False
            return copy.deepcopy(_config)
        # Retry up to 3 times to handle race conditions
        for attempt in range(3):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    on_disk = json.load(f)
                # Merge so new keys from updates are always present
                _config = _deep_merge(DEFAULT_CONFIG, on_disk)
                _cache_valid = False
                logger.info("Config loaded from %s", CONFIG_PATH)
                return copy.deepcopy(_config)
            except Exception as e:
                if attempt < 2:
                    import time
                    time.sleep(0.1)
                else:
                    logger.error("Failed to load config after 3 attempts: %s — using defaults", e)
                    _config = copy.deepcopy(DEFAULT_CONFIG)
                    _cache_valid = False
                    return copy.deepcopy(_config)


def get() -> dict:
    """Return a snapshot of the current config.  Do NOT mutate the result.
    Fast path: returns a cached deepcopy rebuilt only when config changes."""
    global _cache, _cache_valid
    with _lock:
        if not _cache_valid:
            _cache = copy.deepcopy(_config)
            _cache_valid = True
        return _cache


def get_value(*keys, default=None):
    """
    Safely retrieve a nested config value by key path.
    Example: get_value("cpu", "dynamic_throttle")
    """
    with _lock:
        node = _config
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node


def set_value(*keys, value):
    """
    Set a nested config value in memory and persist to disk immediately.
    Example: set_value("display", "overlay_enabled", value=True)
    """
    global _cache_valid
    with _lock:
        node = _config
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value
        _cache_valid = False
        _write_locked(_config)


def save(new_config: dict):
    """Replace entire config with new_config dict and persist."""
    global _config, _cache_valid
    with _lock:
        _config = _deep_merge(DEFAULT_CONFIG, new_config)
        _cache_valid = False
        _write_locked(_config)
    logger.info("Config saved.")


def reload():
    """Re-read config.json from disk into memory. Called after GUI saves."""
    load()
    logger.info("Config reloaded from disk.")


def _write_locked(cfg: dict):
    """Write config to disk atomically. Must be called while _lock is held."""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_PATH)  # atomic on all platforms


def mark_first_run_complete():
    set_value("first_run_complete", value=True)


def is_first_run() -> bool:
    return not get_value("first_run_complete", default=False)
