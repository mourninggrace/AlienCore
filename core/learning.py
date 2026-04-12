"""
AlienCore - learning.py
Observes system behavior over time and generates actionable suggestions.
Data is stored in learning.json (90-day rolling window).
Suggestions are stored in suggestions.json for the Settings inbox.

No AI API required — rule-based engine that gets smarter with more data.
Designed to plug in Claude API later for enhanced reasoning.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from core import config_manager as cfg
from core.constants import BASE_DIR

logger = logging.getLogger("aliencore.learning")

# ── Paths ─────────────────────────────────────────────────────────────────────
LEARNING_PATH    = os.path.join(BASE_DIR, "learning.json")
LEARNING_BACKUP  = os.path.join(BASE_DIR, "learning.json.bak")
SUGGESTIONS_PATH = os.path.join(BASE_DIR, "suggestions.json")

# ── Constants ─────────────────────────────────────────────────────────────────
DATA_RETENTION_DAYS  = 90    # rolling window
MIN_DAYS_BEFORE_SUGGESTIONS = 7   # need at least a week of data
ANALYSIS_INTERVAL_SEC = 3600      # run analysis once per hour
MAX_SUGGESTIONS      = 50         # keep inbox manageable

# ── State ─────────────────────────────────────────────────────────────────────
_thread  = None
_running = False
_lock    = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def start():
    """Start the learning engine background thread."""
    global _thread, _running
    # Write a backup of whatever data exists right now, before we touch anything.
    # This snapshot survives any future crash during the current session.
    with _lock:
        _write_backup_if_due(_load_data())
    _running = True
    _thread  = threading.Thread(target=_loop, daemon=True, name="LearningEngine")
    _thread.start()
    logger.info("Learning engine started.")


def stop():
    global _running
    _running = False


def log_profile_switch(profile: str, trigger: str, readings: dict):
    """Log a profile switch event."""
    event = {
        "type":       "profile_switch",
        "ts":         _now(),
        "profile":    profile,
        "trigger":    trigger,   # "process" | "load" | "manual" | "startup"
        "cpu_temp":   readings.get("cpu_temp_avg"),
        "cpu_load":   readings.get("cpu_load_pct"),
        "gpu_load":   readings.get("gpu_load"),
        "dow":        datetime.now().weekday(),   # 0=Mon 6=Sun
        "hour":       datetime.now().hour,
    }
    _append_event(event)


def log_cpu_ceiling(old_pct: int, new_pct: int, cpu_temp: float, cpu_load: float):
    """Log a CPU ceiling change."""
    event = {
        "type":     "cpu_ceiling",
        "ts":       _now(),
        "old_pct":  old_pct,
        "new_pct":  new_pct,
        "cpu_temp": cpu_temp,
        "cpu_load": cpu_load,
        "hour":     datetime.now().hour,
    }
    _append_event(event)


def log_thermal_event(severity: str, cpu_temp: float, profile: str, ceiling: int):
    """Log when CPU hits warning or critical temp."""
    event = {
        "type":     "thermal_event",
        "ts":       _now(),
        "severity": severity,   # "warn" | "crit"
        "cpu_temp": cpu_temp,
        "profile":  profile,
        "ceiling":  ceiling,
        "hour":     datetime.now().hour,
        "dow":      datetime.now().weekday(),
    }
    _append_event(event)


def log_setting_change(key: str, old_val, new_val, source: str = "user"):
    """Log when a setting is changed."""
    event = {
        "type":    "setting_change",
        "ts":      _now(),
        "key":     key,
        "old_val": str(old_val),
        "new_val": str(new_val),
        "source":  source,   # "user" | "aliencore"
    }
    _append_event(event)


def get_suggestions() -> list:
    """Return all current suggestions from inbox."""
    return _load_suggestions()


def dismiss_suggestion(suggestion_id: str):
    """Mark a suggestion as dismissed."""
    suggestions = _load_suggestions()
    for s in suggestions:
        if s.get("id") == suggestion_id:
            s["dismissed"] = True
            s["dismissed_at"] = _now()
    _save_suggestions(suggestions)


def accept_suggestion(suggestion_id: str) -> Optional[dict]:
    """
    Mark suggestion as accepted and return the action to apply.
    Caller is responsible for applying the action.
    """
    suggestions = _load_suggestions()
    for s in suggestions:
        if s.get("id") == suggestion_id:
            s["accepted"] = True
            s["accepted_at"] = _now()
            _save_suggestions(suggestions)
            return s.get("action")
    return None


def get_insights_summary() -> dict:
    """Return a summary of what AlienCore has learned for the Settings UI."""
    data = _load_data()
    events = data.get("events", [])

    if not events:
        return {"status": "collecting", "message": "Still collecting data — check back after a week of normal use."}

    days_of_data = _days_of_data(events)
    profile_switches = [e for e in events if e["type"] == "profile_switch"]
    thermal_events   = [e for e in events if e["type"] == "thermal_event"]
    ceiling_changes  = [e for e in events if e["type"] == "cpu_ceiling"]

    gaming_sessions   = [e for e in profile_switches if e["profile"] == "gaming"]
    streaming_sessions = [e for e in profile_switches if e["profile"] == "streaming"]

    summary = {
        "status":            "active",
        "days_of_data":      days_of_data,
        "total_events":      len(events),
        "gaming_sessions":   len(gaming_sessions),
        "streaming_sessions": len(streaming_sessions),
        "thermal_warnings":  len([e for e in thermal_events if e["severity"] == "warn"]),
        "thermal_criticals": len([e for e in thermal_events if e["severity"] == "crit"]),
        "ceiling_changes":   len(ceiling_changes),
        "peak_gaming_hours": _peak_hours(gaming_sessions),
        "peak_streaming_hours": _peak_hours(streaming_sessions),
        "pending_suggestions": len([s for s in _load_suggestions()
                                    if not s.get("dismissed") and not s.get("accepted")]),
    }
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# Background loop
# ─────────────────────────────────────────────────────────────────────────────

def _loop():
    """Runs hourly — prunes old data, runs analysis, generates suggestions."""
    # Wait a bit after startup before first analysis
    time.sleep(300)   # 5 minutes

    while _running:
        try:
            _prune_old_events()
            _run_analysis()
            # Refresh backup after each hourly cycle
            with _lock:
                _write_backup_if_due(_load_data())
        except Exception as e:
            logger.error("Learning engine error: %s", e)

        # Sleep until next analysis
        for _ in range(ANALYSIS_INTERVAL_SEC):
            if not _running:
                return
            time.sleep(1)


# ─────────────────────────────────────────────────────────────────────────────
# Analysis rules
# ─────────────────────────────────────────────────────────────────────────────

def _run_analysis():
    """Run all analysis rules and generate suggestions."""
    data   = _load_data()
    events = data.get("events", [])

    if _days_of_data(events) < MIN_DAYS_BEFORE_SUGGESTIONS:
        logger.debug("Learning: not enough data yet (%d days)", _days_of_data(events))
        return

    logger.info("Learning: running analysis on %d events", len(events))

    suggestions = []

    suggestions += _rule_thermal_warnings(events)
    suggestions += _rule_idle_ceiling_too_low(events)
    suggestions += _rule_peak_gaming_hours(events)
    suggestions += _rule_peak_streaming_hours(events)
    suggestions += _rule_frequent_thermal_spikes(events)
    suggestions += _rule_hysteresis_tuning(events)

    # Add new suggestions to inbox (avoid duplicates)
    _add_suggestions(suggestions)


def _rule_thermal_warnings(events: list) -> list:
    """If CPU regularly hits warning temp at idle, suggest lowering trigger."""
    idle_warnings = [
        e for e in events
        if e["type"] == "thermal_event"
        and e["severity"] == "warn"
        and e.get("profile") == "idle"
    ]
    if len(idle_warnings) < 5:
        return []

    c = cfg.get()
    current_trigger = c["cpu"]["throttle_temp_trigger"]
    suggested = max(60, current_trigger - 5)

    if suggested >= current_trigger:
        return []

    return [_make_suggestion(
        sid="thermal_idle_warn",
        title="CPU getting warm at idle",
        message=(
            f"AlienCore has seen your CPU hit the warning temperature "
            f"({current_trigger}°C) {len(idle_warnings)} times while idle over the past "
            f"{_days_of_data(events)} days. Lowering the throttle trigger to "
            f"{suggested}°C would tell AlienCore to start managing the ceiling earlier, "
            f"keeping idle temps cooler."
        ),
        action={"type": "set_config", "key": "cpu.throttle_temp_trigger", "value": suggested},
        action_label=f"Lower trigger to {suggested}°C",
        category="thermal",
    )]


def _rule_idle_ceiling_too_low(events: list) -> list:
    """
    If the CPU has never had a thermal warning at idle and ceiling changes
    are always going UP from the floor, the floor might be too restrictive.
    """
    idle_warnings = [
        e for e in events
        if e["type"] == "thermal_event" and e.get("profile") == "idle"
    ]
    # Only suggest if no idle thermal issues in 14+ days
    if idle_warnings or _days_of_data(events) < 14:
        return []

    c = cfg.get()
    current_ceil = c["cpu"]["idle_max_state_pct"]
    if current_ceil >= 60:
        return []

    suggested = min(60, current_ceil + 10)
    return [_make_suggestion(
        sid="idle_ceiling_relaxed",
        title="Idle CPU ceiling may be too restrictive",
        message=(
            f"After {_days_of_data(events)} days of monitoring, your CPU has never "
            f"hit a thermal warning at idle. The current idle ceiling ({current_ceil}%) "
            f"may be limiting responsiveness unnecessarily. Raising it to {suggested}% "
            f"would allow slightly more headroom while still keeping temps in check."
        ),
        action={"type": "set_config", "key": "cpu.idle_max_state_pct", "value": suggested},
        action_label=f"Raise idle ceiling to {suggested}%",
        category="performance",
    )]


def _rule_peak_gaming_hours(events: list) -> list:
    """If gaming sessions cluster at a consistent time, suggest pre-warming."""
    gaming = [e for e in events if e["type"] == "profile_switch" and e["profile"] == "gaming"]
    if len(gaming) < 7:
        return []

    peak = _peak_hours(gaming)
    if not peak:
        return []

    peak_hour = peak[0]
    # Only suggest if at least 60% of sessions start within a 2-hour window
    in_window = [e for e in gaming if abs(e["hour"] - peak_hour) <= 1]
    if len(in_window) / len(gaming) < 0.6:
        return []

    hour_str = f"{peak_hour}:00"
    return [_make_suggestion(
        sid="peak_gaming_prewarm",
        title=f"You typically game around {hour_str}",
        message=(
            f"AlienCore has noticed {len(in_window)} of your {len(gaming)} gaming "
            f"sessions start around {hour_str}. It could automatically switch to "
            f"gaming profile 5 minutes before this time so your system is already "
            f"optimized when you launch your game. This feature will be added in a "
            f"future update — logging this as a pattern for now."
        ),
        action=None,
        action_label=None,
        category="pattern",
    )]


def _rule_peak_streaming_hours(events: list) -> list:
    """Same as gaming but for streaming."""
    streaming = [e for e in events
                 if e["type"] == "profile_switch" and e["profile"] == "streaming"]
    if len(streaming) < 5:
        return []

    peak = _peak_hours(streaming)
    if not peak:
        return []

    peak_hour = peak[0]
    in_window = [e for e in streaming if abs(e["hour"] - peak_hour) <= 1]
    if len(in_window) / len(streaming) < 0.6:
        return []

    hour_str = f"{peak_hour}:00"
    return [_make_suggestion(
        sid="peak_streaming_prewarm",
        title=f"You typically stream around {hour_str}",
        message=(
            f"AlienCore has seen {len(in_window)} of your {len(streaming)} streaming "
            f"sessions start around {hour_str}. Pattern logged for future pre-warming feature."
        ),
        action=None,
        action_label=None,
        category="pattern",
    )]


def _rule_frequent_thermal_spikes(events: list) -> list:
    """If critical temps are happening regularly, flag it."""
    crits = [e for e in events if e["type"] == "thermal_event" and e["severity"] == "crit"]
    days  = _days_of_data(events)

    if len(crits) < 3 or days < 7:
        return []

    rate = len(crits) / days
    if rate < 0.5:   # less than once every 2 days — not urgent
        return []

    profiles_at_crit = {}
    for e in crits:
        p = e.get("profile", "unknown")
        profiles_at_crit[p] = profiles_at_crit.get(p, 0) + 1
    worst = max(profiles_at_crit, key=profiles_at_crit.get)

    return [_make_suggestion(
        sid="frequent_critical_temps",
        title="Frequent CPU critical temperature events",
        message=(
            f"AlienCore has logged {len(crits)} critical temperature events over "
            f"{days} days — most occurring during {worst} profile. "
            f"This may indicate the CPU throttle trigger needs to be more aggressive, "
            f"or that thermal paste on your laptop may need attention. "
            f"Consider lowering the throttle trigger temperature in Settings → CPU."
        ),
        action=None,
        action_label=None,
        category="thermal",
        priority="high",
    )]


def _rule_hysteresis_tuning(events: list) -> list:
    """
    Analyze ceiling change patterns to see if hysteresis timer needs adjusting.
    If ceiling changes happen very frequently even after the new timer, it's still too short.
    """
    ceiling_events = [e for e in events if e["type"] == "cpu_ceiling"]
    if len(ceiling_events) < 50:
        return []

    # Look at gaps between ceiling changes
    times = sorted([e["ts"] for e in ceiling_events])
    gaps  = [times[i+1] - times[i] for i in range(len(times)-1)]
    avg_gap = sum(gaps) / len(gaps) if gaps else 0

    # If average gap is less than 15 seconds, still thrashing
    if avg_gap > 15:
        return []

    return [_make_suggestion(
        sid="hysteresis_too_short",
        title="CPU ceiling still changing frequently",
        message=(
            f"AlienCore's CPU ceiling is still adjusting on average every "
            f"{avg_gap:.0f} seconds. The hysteresis timer (currently 10s) may need "
            f"to be longer for your workload patterns. This will be auto-tuned in "
            f"a future update — logging the pattern now."
        ),
        action=None,
        action_label=None,
        category="performance",
    )]


# ─────────────────────────────────────────────────────────────────────────────
# Data helpers
# ─────────────────────────────────────────────────────────────────────────────

def _append_event(event: dict):
    with _lock:
        data = _load_data()
        data.setdefault("events", []).append(event)
        _save_data(data)


def _prune_old_events():
    cutoff = time.time() - (DATA_RETENTION_DAYS * 86400)
    with _lock:
        data = _load_data()
        before = len(data.get("events", []))
        data["events"] = [e for e in data.get("events", []) if e.get("ts", 0) > cutoff]
        after = len(data["events"])
        if before != after:
            logger.info("Learning: pruned %d old events", before - after)
            _save_data(data)


def _load_data() -> dict:
    """
    Load learning data.  Tries main file first; if that fails (corrupt/missing)
    falls back to the hourly backup and restores it automatically.
    """
    for path, label in ((LEARNING_PATH, "main"), (LEARNING_BACKUP, "backup")):
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "events" in data:
                if label == "backup":
                    logger.warning(
                        "Learning: main file was corrupt or empty — "
                        "restored %d events from backup %s",
                        len(data["events"]), LEARNING_BACKUP,
                    )
                    _save_data_atomic(data)   # rewrite main file from backup
                return data
        except Exception as e:
            logger.error("Learning: could not read %s (%s): %s", label, path, e)

    # Neither file is readable — start fresh but log loudly
    if os.path.exists(LEARNING_PATH) and os.path.getsize(LEARNING_PATH) > 0:
        logger.error(
            "Learning: data lost — both learning.json and backup unreadable. "
            "Starting fresh. Check disk health."
        )
    return {"events": []}


def _save_data(data: dict):
    """Write data atomically (write temp → rename) to prevent corruption."""
    _save_data_atomic(data)


def _save_data_atomic(data: dict):
    """Atomic write: temp file → os.replace() so a kill can never truncate the live file."""
    tmp = LEARNING_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, LEARNING_PATH)
    except Exception as e:
        logger.error("Learning: save failed: %s", e)
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def _write_backup_if_due(data: dict):
    """
    Write learning.json.bak at most once per hour.
    Called from _loop (runs hourly) and at startup.
    Only writes if we have meaningful data (≥20 events).
    """
    events = data.get("events", [])
    if len(events) < 20:
        return
    try:
        if os.path.exists(LEARNING_BACKUP):
            age = time.time() - os.path.getmtime(LEARNING_BACKUP)
            if age < 3600:
                return
        tmp = LEARNING_BACKUP + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, LEARNING_BACKUP)
        logger.debug("Learning: backup written (%d events)", len(events))
    except Exception as e:
        logger.warning("Learning: backup write failed: %s", e)


def _load_suggestions() -> list:
    try:
        if os.path.exists(SUGGESTIONS_PATH):
            with open(SUGGESTIONS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return []


def _save_suggestions(suggestions: list):
    with open(SUGGESTIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(suggestions, f, indent=2)


def _add_suggestions(new_suggestions: list):
    """Add new suggestions to inbox, avoiding duplicates."""
    existing = _load_suggestions()
    existing_ids = {s["id"] for s in existing}

    added = 0
    for s in new_suggestions:
        if s["id"] not in existing_ids:
            existing.append(s)
            existing_ids.add(s["id"])
            added += 1
            logger.info("Learning: new suggestion — %s", s["title"])
            _send_toast(s)

    if added:
        # Keep inbox at max size — remove oldest dismissed first
        if len(existing) > MAX_SUGGESTIONS:
            dismissed = [s for s in existing if s.get("dismissed")]
            for s in dismissed[:len(existing) - MAX_SUGGESTIONS]:
                existing.remove(s)
        _save_suggestions(existing)


def _make_suggestion(sid: str, title: str, message: str,
                     action, action_label, category: str,
                     priority: str = "normal") -> dict:
    return {
        "id":           sid,
        "title":        title,
        "message":      message,
        "action":       action,
        "action_label": action_label,
        "category":     category,
        "priority":     priority,
        "created_at":   _now(),
        "dismissed":    False,
        "accepted":     False,
    }


def _send_toast(suggestion: dict):
    """Send a Windows toast notification for a new suggestion."""
    try:
        import subprocess
        title   = f"AlienCore Insight: {suggestion['title']}"
        message = suggestion["message"][:100] + "..." if len(suggestion["message"]) > 100 else suggestion["message"]
        script = (
            f"[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
            f"ContentType = WindowsRuntime] | Out-Null; "
            f"$t = [Windows.UI.Notifications.ToastNotificationManager]"
            f"::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
            f"$t.SelectSingleNode('//text[@id=1]').InnerText = '{title}'; "
            f"$t.SelectSingleNode('//text[@id=2]').InnerText = '{message}'; "
            f"$n = [Windows.UI.Notifications.ToastNotification]::new($t); "
            f"[Windows.UI.Notifications.ToastNotificationManager]"
            f"::CreateToastNotifier('AlienCore').Show($n)"
        )
        subprocess.Popen(["powershell", "-Command", script],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        logger.debug("Toast notification failed: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Utility
# ─────────────────────────────────────────────────────────────────────────────

def _now() -> float:
    return time.time()


def _days_of_data(events: list) -> int:
    if not events:
        return 0
    oldest = min(e.get("ts", time.time()) for e in events)
    return max(1, int((time.time() - oldest) / 86400))


def _peak_hours(events: list) -> list:
    """Return hours sorted by frequency."""
    if not events:
        return []
    counts = {}
    for e in events:
        h = e.get("hour", 0)
        counts[h] = counts.get(h, 0) + 1
    return sorted(counts, key=counts.get, reverse=True)
