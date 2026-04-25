"""
AlienCore - ai_models.py

Live model-list fetcher for the Settings → AI tab.

Hits each provider's /v1/models endpoint when the user has an API key set,
caches the result for an hour, and falls back to a hardcoded list when:
  - no API key is set
  - the network call fails or times out
  - the response is malformed
  - the provider returns an empty list

Both Anthropic and OpenAI-compatible endpoints expose /v1/models with the
same {"data": [{"id": "...", ...}, ...]} envelope, so the parsing is
shared. Anthropic results are bucketed (opus → sonnet → haiku) so the
flagship surfaces at the top of the dropdown; OpenAI native is filtered
to chat-class models (gpt-*, o1/o3/o4-*) so the dropdown isn't polluted
with embedding/whisper/dall-e/tts model IDs.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
import urllib.error

logger = logging.getLogger("aliencore.ai_models")

# Hardcoded fallback lists — used when the API can't be reached. Update
# these when new flagship models ship; the live fetch surfaces fresh
# releases automatically once a key is set, so these only matter on the
# very first launch (no key yet) or in fully offline use.
FALLBACK_ANTHROPIC = [
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
]

FALLBACK_OPENAI = [
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-turbo",
    "gpt-3.5-turbo",
    "llama-3.3-70b-versatile",
    "mixtral-8x7b-32768",
    "mistral-large-latest",
    "grok-2-latest",
]

_CACHE_TTL = 3600  # 1 hour — model catalogs don't churn fast
_TIMEOUT   = 6.0   # seconds; we'd rather fall back than block the UI

# key = (provider, base_url-or-empty)
# val = (fetch_timestamp, [model_id, ...])
_cache: dict = {}
_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def fallback(provider: str) -> list[str]:
    """Hardcoded list — instant, no network."""
    return list(FALLBACK_ANTHROPIC if provider == "anthropic"
                else FALLBACK_OPENAI)


def fetch_models(provider: str,
                 api_key: str,
                 base_url: str = "",
                 *,
                 force_refresh: bool = False) -> list[str]:
    """
    Return a list of model IDs available from the configured provider.
    Falls back to FALLBACK_* on any failure (no key, timeout, parse error).
    Cached for one hour per (provider, base_url) pair.
    """
    if not api_key:
        return fallback(provider)

    cache_key = (provider, (base_url or "").rstrip("/"))
    now = time.time()

    if not force_refresh:
        with _lock:
            cached = _cache.get(cache_key)
            if cached and (now - cached[0]) < _CACHE_TTL:
                return list(cached[1])

    try:
        if provider == "anthropic":
            ids = _fetch_anthropic(api_key)
        else:
            ids = _fetch_openai_compat(api_key, base_url)
    except Exception as e:
        logger.debug("Model fetch failed (%s): %s — using fallback",
                     provider, e)
        return fallback(provider)

    if not ids:
        return fallback(provider)

    with _lock:
        _cache[cache_key] = (now, list(ids))
    return ids


def clear_cache():
    """Forget every cached fetch — next call will hit the network again."""
    with _lock:
        _cache.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Provider fetchers
# ─────────────────────────────────────────────────────────────────────────────

def _http_json(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body)


def _fetch_anthropic(api_key: str) -> list[str]:
    """GET https://api.anthropic.com/v1/models — filters to claude-* ids."""
    data = _http_json(
        "https://api.anthropic.com/v1/models",
        {
            "x-api-key":         api_key,
            "anthropic-version": "2023-06-01",
            "User-Agent":        "AlienCore/1.0",
        },
    )
    models = data.get("data") or []
    ids = [m.get("id") for m in models if m.get("id")]
    ids = [m for m in ids if m.startswith("claude-")]
    return _sort_anthropic(ids)


def _sort_anthropic(ids: list[str]) -> list[str]:
    """
    Bucket by tier (opus → sonnet → haiku → other) then sort each bucket
    by descending string so newer versions float to the top within tier.
      claude-opus-4-8 → above claude-opus-4-7 → above claude-opus-3
    """
    buckets: dict[str, list[str]] = {
        "opus": [], "sonnet": [], "haiku": [], "other": [],
    }
    for mid in ids:
        for name in ("opus", "sonnet", "haiku"):
            if name in mid:
                buckets[name].append(mid)
                break
        else:
            buckets["other"].append(mid)
    for k in buckets:
        buckets[k].sort(reverse=True)
    return (buckets["opus"]
            + buckets["sonnet"]
            + buckets["haiku"]
            + buckets["other"])


def _fetch_openai_compat(api_key: str, base_url: str) -> list[str]:
    """
    GET {base_url or https://api.openai.com/v1}/models
    For native OpenAI: filter to chat-class IDs (gpt-*, o1/o3/o4-*).
    For third-party endpoints (Groq, Together, Ollama, etc.) return
    everything the API lists — the user picked the provider and knows
    what they want.
    """
    base = (base_url or "https://api.openai.com/v1").rstrip("/")
    url = f"{base}/models"
    data = _http_json(url, {
        "Authorization": f"Bearer {api_key}",
        "User-Agent":    "AlienCore/1.0",
    })
    models = data.get("data") or []
    ids = [m.get("id") for m in models if m.get("id")]
    if not ids:
        return []

    is_native_openai = (not base_url) or "api.openai.com" in base_url
    if is_native_openai:
        ids = [m for m in ids if _is_openai_chat_model(m)]

    return sorted(ids, reverse=True)


def _is_openai_chat_model(mid: str) -> bool:
    """Keep gpt-* and reasoning models (o1/o3/o4-*); drop embeddings, tts, etc."""
    if mid.startswith("gpt-"):
        return True
    # o-series reasoning models: o1-..., o3-..., o4-...
    if len(mid) >= 2 and mid[0] == "o" and mid[1].isdigit():
        return True
    return False
