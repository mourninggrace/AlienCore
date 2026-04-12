"""
AlienCore - ai_advisor.py
Config-level AI advisor.

Flow:
  1. Snapshot current config (tunable keys only) + live sensors + hardware
  2. Send to AI with a strict JSON-response schema
  3. Parse + validate response against the allowed key/type/range table
  4. Return a list of Proposal objects for the UI to display
  5. On user approval: backup → apply → log

Only keys listed in TUNABLE (or returned by _dynamic_tunables()) can ever be
changed.  The AI is told exactly what keys exist and what ranges/values are
valid, so hallucinated keys are silently dropped.

TUNABLE tuple format:
  (human_label, python_type, min_or_allowed, max)
  • bool   — min/max ignored
  • int    — min and max are inclusive bounds
  • float  — min and max are inclusive bounds
  • str    — min is a list of allowed values, max is None
"""

import json
import logging
import os
import re
import shutil
from datetime import datetime
from core import config_manager as cfg
from core.constants import CONFIG_PATH

logger = logging.getLogger("aliencore.advisor")

BACKUP_PATH = CONFIG_PATH.replace(".json", ".advisor_backup.json")
LOG_PATH    = os.path.join(os.path.dirname(CONFIG_PATH), "logs", "ai_changes.log")

# ─────────────────────────────────────────────────────────────────────────────
# Tunable key registry  (static flat keys)
# (human_label, type, min_or_allowed, max)  — see module docstring
# ─────────────────────────────────────────────────────────────────────────────

TUNABLE = {
    # ── Thresholds ────────────────────────────────────────────────────────────
    "thresholds.cpu_warn":              ("CPU warn temp (°C)",                  int,   50,  95),
    "thresholds.cpu_crit":              ("CPU critical temp (°C)",              int,   60, 100),
    "thresholds.gpu_warn":              ("GPU warn temp (°C)",                  int,   50,  95),
    "thresholds.gpu_crit":              ("GPU critical temp (°C)",              int,   60, 100),
    "thresholds.nvme_warn":             ("NVMe warn temp (°C)",                 int,   35,  70),
    "thresholds.nvme_crit":             ("NVMe critical temp (°C)",             int,   45,  80),
    # ── CPU ──────────────────────────────────────────────────────────────────
    "cpu.dynamic_throttle":             ("Dynamic CPU throttle",                bool, None, None),
    "cpu.idle_max_state_pct":           ("Idle CPU ceiling (%)",                int,   10, 100),
    "cpu.throttle_temp_trigger":        ("Throttle trigger temp (°C)",          int,   50,  92),
    "cpu.full_power_in_gaming":         ("Full CPU power in gaming",            bool, None, None),
    "cpu.full_power_in_streaming":      ("Full CPU power in streaming",         bool, None, None),
    "cpu.hetero_scheduling":            ("Intel Thread Director",               bool, None, None),
    "cpu.core_parking_gaming":          ("Unpark all cores in gaming",          bool, None, None),
    # ── GPU ──────────────────────────────────────────────────────────────────
    "gpu.fan_curve_enabled":            ("Custom GPU fan curve",                bool, None, None),
    "gpu.power_limit_idle_pct":         ("GPU power at idle (%)",               int,   20, 100),
    "gpu.power_limit_gaming_pct":       ("GPU power in gaming (%)",             int,   50, 100),
    "gpu.power_limit_streaming_pct":    ("GPU power in streaming (%)",          int,   50, 100),
    "gpu.hags_enabled":                 ("Hardware-Accel GPU Scheduling",       bool, None, None),
    "gpu.powermizer_max_performance":   ("NVIDIA max-perf P-state",             bool, None, None),
    # ── RAM ──────────────────────────────────────────────────────────────────
    "ram.disable_superfetch":           ("Disable SysMain (Superfetch)",        bool, None, None),
    "ram.clear_standby_cache_on_idle":  ("Clear standby cache at idle",         bool, None, None),
    "ram.disable_paging_executive":     ("Pin kernel in RAM",                   bool, None, None),
    # ── Scheduler ────────────────────────────────────────────────────────────
    "scheduler.ecoqos_background":      ("Route background procs to E-cores",  bool, None, None),
    "scheduler.win32_priority_separation":("Win32 quantum tuning",             bool, None, None),
    # ── Network ──────────────────────────────────────────────────────────────
    "network.disable_nagle":            ("Disable Nagle algorithm",             bool, None, None),
    "network.disable_rsc":              ("Disable Recv Segment Coalescing",     bool, None, None),
    "network.disable_ecn":              ("Disable ECN",                        bool, None, None),
    "network.tcp_socket_tuning":        ("TCP socket tuning tweaks",            bool, None, None),
    "network.qos_reserve_fix":          ("Remove 20% QoS bandwidth hold",      bool, None, None),
    "network.flush_dns_on_switch":      ("Flush DNS on profile switch",         bool, None, None),
    "network.nic_interrupt_moderation": ("Disable NIC interrupt moderation",    bool, None, None),
    "network.mmcss_tuning":             ("MMCSS network tuning",                bool, None, None),
    # ── Visual ───────────────────────────────────────────────────────────────
    "visual.disable_animations":        ("Disable Windows animations",          bool, None, None),
    "visual.disable_transparency":      ("Disable Windows transparency",        bool, None, None),
    # ── Display ──────────────────────────────────────────────────────────────
    "display.update_interval_value":    ("Sensor update interval",              float, 0.5, 10.0),
    "display.auto_hide_fullscreen":     ("Bar auto-hide in fullscreen",         bool, None, None),
    "display.temp_unit":                ("Temperature unit",                    str,
                                         ["celsius", "fahrenheit"],             None),
    # ── Profile detection ─────────────────────────────────────────────────────
    "profiles.gaming_gpu_threshold":    ("Gaming GPU% threshold",               int,   10,  80),
    "profiles.gaming_cpu_threshold":    ("Gaming CPU% threshold",               int,   10,  80),
    "profiles.detect_by_load":          ("Load-based profile detection",        bool, None, None),
    # ── Turbo Cool ───────────────────────────────────────────────────────────
    "turbo_cool.auto_off_minutes":      ("Turbo Cool auto-off (min)",           int,    1,  60),
    "turbo_cool.auto_off_on_cool":      ("Turbo Cool auto-off when cool",       bool, None, None),
    # ── AWCC ─────────────────────────────────────────────────────────────────
    "awcc.gmode_on_gaming":             ("G-Mode in gaming (loud)",             bool, None, None),
    "awcc.sync_profile":                ("Sync AWCC thermal profile",           bool, None, None),
    # ── Service ──────────────────────────────────────────────────────────────
    "service.notify_on_profile_switch": ("Toast notification on profile switch",bool, None, None),
    # ── AI watchdog ──────────────────────────────────────────────────────────
    "ai.watchdog_interval_sec":         ("AI watchdog interval (sec)",          int,   60, 1800),
}


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic tunables — user-defined profile behaviors
# Key format: "user_profile:{name}.behavior"
# ─────────────────────────────────────────────────────────────────────────────

_BEHAVIOR_VALUES = ["idle", "gaming", "streaming"]


def _dynamic_tunables() -> dict:
    """
    Return TUNABLE-style entries for each user-defined profile's behavior field.
    Called at runtime so new profiles are picked up without restart.
    """
    result = {}
    for up in cfg.get().get("profiles", {}).get("user_profiles", []):
        name = up.get("name", "").strip()
        if not name:
            continue
        label = up.get("label", name)
        key   = f"user_profile:{name}.behavior"
        result[key] = (
            f"'{label}' profile behavior",
            str,
            _BEHAVIOR_VALUES,
            None,
        )
    return result


def _all_tunables() -> dict:
    """Static TUNABLE merged with dynamic user-profile behavior keys."""
    combined = dict(TUNABLE)
    combined.update(_dynamic_tunables())
    return combined


# ─────────────────────────────────────────────────────────────────────────────
# Config accessors — support both flat dot-keys and user_profile: prefix
# ─────────────────────────────────────────────────────────────────────────────

def _cfg_get(dot_key: str):
    if dot_key.startswith("user_profile:"):
        # "user_profile:{name}.{field}"
        rest        = dot_key[len("user_profile:"):]
        name, field = rest.rsplit(".", 1)
        for up in cfg.get().get("profiles", {}).get("user_profiles", []):
            if up.get("name") == name:
                return up.get(field)
        return None

    keys = dot_key.split(".")
    node = cfg.get()
    for k in keys:
        if isinstance(node, dict) and k in node:
            node = node[k]
        else:
            return None
    return node


def _cfg_set(dot_key: str, value):
    if dot_key.startswith("user_profile:"):
        rest        = dot_key[len("user_profile:"):]
        name, field = rest.rsplit(".", 1)
        c = cfg.get()
        for up in c.get("profiles", {}).get("user_profiles", []):
            if up.get("name") == name:
                up[field] = value
                cfg.save(c)
                return
        logger.warning("_cfg_set: user profile %r not found", name)
        return

    keys = dot_key.split(".")
    node = cfg.get()
    # Walk to the parent dict, then set
    # We need to work on the live _config, not a snapshot
    import core.config_manager as _cm
    with _cm._lock:
        node = _cm._config
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value


# ─────────────────────────────────────────────────────────────────────────────
# Config snapshot
# ─────────────────────────────────────────────────────────────────────────────

def _config_snapshot() -> dict:
    """Return current values for all tunable keys (static + dynamic)."""
    return {key: _cfg_get(key) for key in _all_tunables()}


# ─────────────────────────────────────────────────────────────────────────────
# Proposal data class
# ─────────────────────────────────────────────────────────────────────────────

class Proposal:
    def __init__(self, key: str, label: str, current, proposed, reason: str):
        self.key      = key
        self.label    = label
        self.current  = current
        self.proposed = proposed
        self.reason   = reason

    def __repr__(self):
        return f"Proposal({self.key}: {self.current!r} → {self.proposed!r})"


# ─────────────────────────────────────────────────────────────────────────────
# Prompt builder
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA = """{
  "assessment": "<2–3 sentence summary of system state and what you're addressing>",
  "changes": [
    {
      "key": "<exact key from the allowed list>",
      "proposed": <new value — number, true/false, or quoted string>,
      "reason": "<one concise sentence>"
    }
  ]
}"""

_INSTRUCTIONS = """You are AlienCore Config Advisor.
Analyze the system state and propose config changes that will improve thermal management, performance, or stability.

Rules (strictly enforced):
1. Respond ONLY with valid JSON matching the schema. No preamble, no markdown.
2. Only use keys from the ALLOWED KEYS list — any other key is ignored.
3. Boolean keys must use JSON true or false (not strings).
4. Numeric keys must stay within the stated range.
5. String keys must use one of the listed allowed values exactly.
6. Only propose a change if there is clear evidence for it in the sensor data or current values.
7. If the system looks well-configured, return an empty changes array.
8. Maximum 8 changes per response.
9. Do not propose the same value that is already set.

Response schema:
""" + _SCHEMA


def build_prompt() -> str:
    from core import ai_manager
    all_t        = _all_tunables()
    config_snap  = _config_snapshot()
    sensor_snap  = ai_manager.get_system_context()
    machine      = ai_manager._machine_label()

    allowed_lines = []
    for key, (label, typ, mn, mx) in all_t.items():
        current = config_snap.get(key)
        if typ == bool:
            rng = "true | false"
        elif typ == str:
            rng = " | ".join(mn) if isinstance(mn, list) else str(mn)
        else:
            rng = f"{mn}–{mx}"
        allowed_lines.append(
            f"  {key:<50}  current={current!r:<12}  range={rng}  ({label})"
        )

    return (
        f"{_INSTRUCTIONS}\n\n"
        f"HARDWARE:\n{machine}\n\n"
        f"LIVE SENSOR STATE:\n{sensor_snap}\n\n"
        f"ALLOWED KEYS (only these may appear in 'changes'):\n"
        + "\n".join(allowed_lines)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Response parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_response(raw: str) -> tuple:
    """
    Parse AI response.  Returns (assessment: str, proposals: list[Proposal]).
    Drops any proposed change with an unknown key, wrong type, or bad value.
    """
    assessment = ""
    proposals  = []

    # Strip markdown code fences if present
    json_text = raw.strip()
    m = re.search(r"\{.*\}", json_text, re.DOTALL)
    if not m:
        return raw.strip(), []
    json_text = m.group(0)

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        logger.warning("Advisor JSON parse error: %s", e)
        return raw.strip(), []

    assessment  = data.get("assessment", "").strip()
    raw_changes = data.get("changes", [])
    all_t       = _all_tunables()
    config_snap = _config_snapshot()

    for ch in raw_changes:
        key      = ch.get("key", "")
        proposed = ch.get("proposed")
        reason   = ch.get("reason", "").strip()

        if key not in all_t:
            logger.debug("Advisor: ignoring unknown key %r", key)
            continue

        label, typ, mn, mx = all_t[key]
        current = config_snap.get(key)

        try:
            if typ == bool:
                if not isinstance(proposed, bool):
                    proposed = str(proposed).lower() in ("true", "1", "yes")
            elif typ == int:
                proposed = int(proposed)
                if not (mn <= proposed <= mx):
                    logger.debug("Advisor: %r value %r out of range [%s, %s]",
                                 key, proposed, mn, mx)
                    continue
            elif typ == float:
                proposed = float(proposed)
                if not (mn <= proposed <= mx):
                    logger.debug("Advisor: %r value %r out of range", key, proposed)
                    continue
            elif typ == str:
                proposed = str(proposed).strip().lower()
                allowed  = mn if isinstance(mn, list) else []
                if allowed and proposed not in allowed:
                    logger.debug("Advisor: %r value %r not in allowed %r",
                                 key, proposed, allowed)
                    continue
        except (TypeError, ValueError):
            logger.debug("Advisor: bad type for %r: %r", key, proposed)
            continue

        if proposed == current:
            logger.debug("Advisor: %r already at %r — skipping", key, proposed)
            continue

        proposals.append(Proposal(key, label, current, proposed, reason))

    return assessment, proposals


# ─────────────────────────────────────────────────────────────────────────────
# Apply / rollback
# ─────────────────────────────────────────────────────────────────────────────

def apply_proposals(proposals: list) -> int:
    """
    Backup config, apply the given proposals, save, and log.
    Returns the number of changes applied.
    """
    if not proposals:
        return 0

    try:
        shutil.copy2(CONFIG_PATH, BACKUP_PATH)
        logger.info("Config backed up to %s", BACKUP_PATH)
    except Exception as e:
        logger.warning("Backup failed: %s — applying anyway", e)

    applied = []
    for p in proposals:
        try:
            _cfg_set(p.key, p.proposed)
            applied.append(p)
            logger.info("Advisor applied: %s = %r  (was %r)  — %s",
                        p.key, p.proposed, p.current, p.reason)
        except Exception as e:
            logger.error("Advisor apply error for %s: %s", p.key, e)

    cfg.save(cfg.get())
    _write_log(applied)
    return len(applied)


def rollback() -> bool:
    """Restore config from the last advisor backup. Returns True on success."""
    if not os.path.exists(BACKUP_PATH):
        return False
    try:
        shutil.copy2(BACKUP_PATH, CONFIG_PATH)
        cfg.reload()
        logger.info("Advisor rollback complete.")
        return True
    except Exception as e:
        logger.error("Rollback failed: %s", e)
        return False


def backup_exists() -> bool:
    return os.path.exists(BACKUP_PATH)


def backup_timestamp() -> str:
    try:
        t = os.path.getmtime(BACKUP_PATH)
        return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def _write_log(applied: list):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"\n[{ts}] AI Advisor applied {len(applied)} change(s):\n")
            for p in applied:
                f.write(f"  {p.key}: {p.current!r} → {p.proposed!r}  |  {p.reason}\n")
    except Exception as e:
        logger.warning("Could not write change log: %s", e)


def get_change_log() -> str:
    try:
        if os.path.exists(LOG_PATH):
            with open(LOG_PATH, "r", encoding="utf-8") as f:
                return f.read().strip()
    except Exception:
        pass
    return "No changes recorded yet."


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def analyze() -> tuple:
    """
    Build prompt, call AI, parse response.
    Returns (assessment: str, proposals: list[Proposal]).
    Raises RuntimeError on API error.
    """
    from core import ai_manager

    prompt = build_prompt()
    raw    = ai_manager.send_chat(prompt, [])

    if raw.startswith("Error:"):
        raise RuntimeError(raw)

    assessment, proposals = parse_response(raw)
    logger.info("Advisor: %d proposal(s) after analysis", len(proposals))
    return assessment, proposals
