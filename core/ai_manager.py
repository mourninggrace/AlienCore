"""
AlienCore - ai_manager.py
AI integration layer: chat + background watchdog.

Two provider modes:
  anthropic      — Claude via the anthropic SDK  (pip install anthropic)
  openai_compat  — Any OpenAI-compatible REST endpoint via the openai SDK
                   (pip install openai)
                   Covers: OpenAI, Groq, Mistral, Together, Perplexity,
                           Ollama (local), LM Studio, Cohere, xAI, etc.
                   Set base_url to the provider's v1 endpoint.

Watchdog only fires the API when sensor readings are notably elevated,
and uses the model configured under ai.watchdog_model (falls back to
ai.model, then a sensible default per provider).
"""

import json
import logging
import threading
import time
from datetime import datetime
from core import config_manager as cfg

logger = logging.getLogger("aliencore.ai")

_watchdog_thread = None
_running         = False
_last_alert      = [None]   # [str | None]

PROVIDERS = {
    "anthropic":     "Anthropic (Claude)",
    "openai_compat": "OpenAI-compatible endpoint",
}

# Fallback models when the user leaves the field blank
_DEFAULT_CHAT_MODEL = {
    "anthropic":     "claude-sonnet-4-6",
    "openai_compat": "gpt-4o",          # user should override for non-OpenAI
}
_DEFAULT_WATCHDOG_MODEL = {
    "anthropic":     "claude-haiku-4-5-20251001",
    "openai_compat": "gpt-4o-mini",     # user should override for non-OpenAI
}

# ─────────────────────────────────────────────────────────────────────────────
# Watchdog lifecycle
# ─────────────────────────────────────────────────────────────────────────────

def start_watchdog():
    global _watchdog_thread, _running
    if _running:
        return
    _running = True
    _watchdog_thread = threading.Thread(
        target=_watchdog_loop, daemon=True, name="AIWatchdogThread"
    )
    _watchdog_thread.start()
    logger.info("AI watchdog started.")


def stop_watchdog():
    global _running
    _running = False


def get_last_alert() -> str | None:
    return _last_alert[0]


def clear_alert():
    _last_alert[0] = None


# ─────────────────────────────────────────────────────────────────────────────
# System context snapshot
# ─────────────────────────────────────────────────────────────────────────────

def _machine_label() -> str:
    """
    Build a human-readable machine description from the hardware cache.
    Falls back gracefully if the cache is missing or incomplete.
    """
    try:
        from core import hardware
        hw   = hardware.get_cached()
        cpu  = hw.get("cpu", {}).get("name", "Unknown CPU")
        ram  = hw.get("ram", {}).get("total_gb", "?")
        os_n = hw.get("os",  {}).get("name", "Windows")
        gpus = hw.get("gpu", [])
        gpu  = gpus[0].get("name", "Unknown GPU") if gpus else "Unknown GPU"
        return f"{cpu}, {gpu}, {ram}GB RAM, {os_n}"
    except Exception:
        return "Alienware gaming laptop, Windows 11"


def get_system_context() -> str:
    """Compact JSON snapshot of live sensor state sent with every API call."""
    from core import sensors, profiles
    readings = sensors.get_readings()
    profile  = profiles.get_current()
    c        = cfg.get()
    thresh   = c.get("thresholds", {})

    snap = {
        "ts":          datetime.now().strftime("%H:%M:%S"),
        "profile":     profile,
        "cpu_temp":    readings.get("cpu_temp_avg"),
        "cpu_load":    readings.get("cpu_load_pct"),
        "cpu_freq_ghz":readings.get("cpu_freq_ghz"),
        "cpu_watts":   readings.get("cpu_watts"),
        "gpu_temp":    readings.get("gpu_temp"),
        "gpu_hotspot": readings.get("gpu_temp_hotspot"),
        "gpu_load":    readings.get("gpu_load"),
        "gpu_watts":   readings.get("gpu_watts"),
        "ram_pct":     readings.get("ram_usage_pct"),
        "nvme_max_c":  max((n["temp_c"] for n in readings.get("nvme_temps", [])), default=None),
        "battery_pct": readings.get("battery_pct"),
        "battery_chg": readings.get("battery_charging"),
        "thresholds":  thresh,
        "machine":     _machine_label(),
    }
    return json.dumps({k: v for k, v in snap.items() if v is not None},
                      separators=(",", ":"))


# ─────────────────────────────────────────────────────────────────────────────
# Chat
# ─────────────────────────────────────────────────────────────────────────────

def _build_system_prompt() -> str:
    return (
        "You are AlienCore AI, an intelligent assistant embedded in a Windows system optimizer "
        f"for a gaming laptop ({_machine_label()}). "
        "You have live access to sensor readings injected at the start of each message. "
        "Help the user understand settings, diagnose thermal or performance issues, and suggest "
        "optimizations. Be concise and direct. If sensor data looks healthy, say so. "
        "If something is concerning, explain why and what to do."
    )


def send_chat(user_message: str, history: list) -> str:
    """
    Send a chat message and return the assistant reply string.
    history: list of {"role": "user"|"assistant", "content": str}
    Returns the full reply, or a string starting with "Error:" on failure.
    """
    c       = cfg.get().get("ai", {})
    ai_cfg  = _resolve_config(c, chat=True)

    if not ai_cfg["api_key"]:
        return "Error: No API key configured — go to Settings → AI."

    context = get_system_context()
    system  = f"{_build_system_prompt()}\n\nCurrent system snapshot: {context}"

    try:
        if ai_cfg["provider"] == "anthropic":
            return _anthropic_call(ai_cfg, system, history, user_message)
        else:
            return _openai_compat_call(ai_cfg, system, history, user_message)
    except Exception as e:
        logger.error("AI chat error: %s", e)
        return f"Error: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# Watchdog
# ─────────────────────────────────────────────────────────────────────────────

def _build_watchdog_system() -> str:
    return (
        f"You are a terse system health watchdog for a gaming laptop ({_machine_label()}). "
        "Reply with exactly 'OK' if everything looks healthy. "
        "If there is a genuine concern, reply with ONE short sentence — the recommendation only, "
        "no preamble. Never recommend things already handled by the optimizer."
    )


def watchdog_analyze() -> str | None:
    """
    Analyze current system state via AI.
    Returns None if healthy, or a short recommendation string.
    """
    c      = cfg.get().get("ai", {})
    ai_cfg = _resolve_config(c, chat=False)

    if not ai_cfg["api_key"]:
        return None

    context = get_system_context()
    prompt  = (
        f"System snapshot: {context}\n\n"
        "Analyze. Reply 'OK' if healthy, or one short actionable sentence if not."
    )

    watchdog_system = _build_watchdog_system()
    try:
        if ai_cfg["provider"] == "anthropic":
            result = _anthropic_call(ai_cfg, watchdog_system, [], prompt)
        else:
            result = _openai_compat_call(ai_cfg, watchdog_system, [], prompt)
    except Exception as e:
        logger.debug("Watchdog API error: %s", e)
        return None

    result = result.strip()
    if result.upper().startswith("OK"):
        return None
    logger.info("AI watchdog alert: %s", result)
    return result


def _watchdog_loop():
    while _running:
        try:
            c        = cfg.get().get("ai", {})
            enabled  = c.get("watchdog_enabled", False)
            interval = max(60, int(c.get("watchdog_interval_sec", 300)))

            if enabled:
                from core import sensors
                readings = sensors.get_readings()
                thresh   = cfg.get().get("thresholds", {})
                cpu_t    = readings.get("cpu_temp_avg") or 0
                gpu_t    = readings.get("gpu_temp") or 0
                cpu_l    = readings.get("cpu_load_pct") or 0

                notable = (
                    cpu_t > thresh.get("cpu_warn", 80) - 5 or
                    gpu_t > thresh.get("gpu_warn", 80) - 5 or
                    cpu_l > 85
                )
                if notable:
                    alert = watchdog_analyze()
                    if alert:
                        _last_alert[0] = alert
        except Exception as e:
            logger.debug("Watchdog loop error: %s", e)

        time.sleep(interval)


# ─────────────────────────────────────────────────────────────────────────────
# Low-level API calls
# ─────────────────────────────────────────────────────────────────────────────

def _anthropic_call(ai_cfg: dict, system: str, history: list, message: str) -> str:
    try:
        import anthropic
    except ImportError:
        return "Error: anthropic package not installed.  Run: pip install anthropic"
    try:
        client   = anthropic.Anthropic(api_key=ai_cfg["api_key"])
        messages = list(history) + [{"role": "user", "content": message}]
        resp = client.messages.create(
            model=ai_cfg["model"],
            max_tokens=1024,
            system=system,
            messages=messages,
        )
        return resp.content[0].text
    except Exception as e:
        return f"Error: {e}"


def _openai_compat_call(ai_cfg: dict, system: str, history: list, message: str) -> str:
    """Works with any OpenAI-compatible endpoint (OpenAI, Groq, Ollama, etc.)."""
    try:
        import openai
    except ImportError:
        return "Error: openai package not installed.  Run: pip install openai"
    try:
        kwargs = {"api_key": ai_cfg["api_key"]}
        if ai_cfg.get("base_url"):
            kwargs["base_url"] = ai_cfg["base_url"]

        client   = openai.OpenAI(**kwargs)
        messages = [{"role": "system", "content": system}]
        messages += list(history)
        messages.append({"role": "user", "content": message})
        resp = client.chat.completions.create(
            model=ai_cfg["model"],
            messages=messages,
            max_tokens=1024,
        )
        return resp.choices[0].message.content
    except Exception as e:
        return f"Error: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# Config resolver
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_config(c: dict, chat: bool) -> dict:
    """
    Build a normalized dict with all fields needed for an API call.
    chat=True → prefer ai.model / full model
    chat=False → prefer ai.watchdog_model / cheap model
    """
    provider  = c.get("provider", "anthropic")
    api_key   = c.get("api_key", "").strip()
    base_url  = c.get("base_url", "").strip()

    if chat:
        defaults = _DEFAULT_CHAT_MODEL
        model    = (c.get("model", "").strip()
                    or defaults.get(provider, ""))
    else:
        defaults = _DEFAULT_WATCHDOG_MODEL
        model    = (c.get("watchdog_model", "").strip()
                    or c.get("model", "").strip()
                    or defaults.get(provider, ""))

    return {
        "provider": provider,
        "api_key":  api_key,
        "base_url": base_url,
        "model":    model,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Connection test
# ─────────────────────────────────────────────────────────────────────────────

def test_connection() -> str:
    """Quick connectivity test. Returns a status string."""
    c      = cfg.get().get("ai", {})
    ai_cfg = _resolve_config(c, chat=True)

    if not ai_cfg["api_key"]:
        return "No API key entered."

    reply = send_chat("Reply with exactly the words: AlienCore connected", [])
    if reply.startswith("Error:"):
        return reply

    provider_label = PROVIDERS.get(ai_cfg["provider"], ai_cfg["provider"])
    model          = ai_cfg["model"]
    base           = f"  [{ai_cfg['base_url']}]" if ai_cfg.get("base_url") else ""
    return f"Connected  ·  {provider_label}  ·  {model}{base}"
