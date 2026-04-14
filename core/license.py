"""
AlienCore - license.py
Feature gate checks.  Import and call check() / is_allowed() anywhere a
feature needs to be gated behind a purchase.

Tier definitions
────────────────
  BASE ($20 one-time)  — all core hardware features
  PRO  (+$5 add-on)    — AI integration (chat, watchdog, advisor)

To change which features live in each tier, edit the sets below.
"""

from core import auth

_TRIAL_DAYS = 30   # must match auth._TRIAL_DAYS

# ── Features that require the base license ($19.99) ──────────────────────────
BASE_FEATURES: set[str] = {
    # RAM — basic visibility panels
    "ram_composition",
    "ram_unified_pressure",
    # Tabs
    "tab_insights",
    "tab_drivers",
    "tab_custom_profiles",
}

# ── Features that require the Pro add-on (+$5) ───────────────────────────────
PRO_FEATURES: set[str] = {
    # AI
    "ai_chat",
    "ai_watchdog",
    "ai_advisor",
    # CPU advanced
    "cpu_tvb_optimizer",
    "cpu_interrupt_steering",
    "cpu_boost_score",
    "cpu_topology",
    # GPU advanced
    "gpu_dynamic_boost",
    "gpu_vram_clock_lock",
    "gpu_throttle_log",
    "gpu_efficiency_curve",
    "gpu_driver_features",
    # RAM advanced
    "ram_working_set_trimmer",
    "ram_leak_watchdog",
    "ram_dimm_protection",
    "ram_pagefile_advisor",
    # Services
    "services_management",
}


class LicenseError(Exception):
    """Raised by require() when a feature isn't licensed."""


def check(feature: str) -> tuple[bool, str | None]:
    """
    Returns (allowed: bool, reason: str | None).
    reason is None when allowed; a human-readable message when blocked.

    Access rules:
      • Not logged in        → blocked (all features)
      • On trial             → base features allowed; Pro features blocked
      • Licensed (paid base) → base features allowed
      • Pro add-on           → all features allowed
    """
    if not auth.is_logged_in():
        return False, "Sign in to use this feature."

    on_trial = auth.is_on_trial()

    # Pro features — never available on trial
    if feature in PRO_FEATURES:
        if not auth.is_pro():
            if on_trial:
                days = auth.trial_days_left()
                return False, (
                    f"[PRO]  This feature requires the Pro add-on (+$4.99).  "
                    f"You have {days} trial day(s) remaining on base features."
                )
            return False, "[PRO]  This feature requires the Pro add-on (+$4.99)."

    # Base features — allowed on trial OR with paid license
    if not auth.is_licensed() and not on_trial:
        if auth.trial_days_left() == 0 and auth.get_session().get("trial_started_at"):
            return False, "Your 30-day free trial has expired. Purchase AlienCore to continue ($19.99)."
        return False, "This feature requires AlienCore ($19.99 one-time purchase)."

    return True, None


def is_allowed(feature: str) -> bool:
    ok, _ = check(feature)
    return ok


def require(feature: str):
    """Raise LicenseError if the feature isn't accessible."""
    ok, reason = check(feature)
    if not ok:
        raise LicenseError(reason)
