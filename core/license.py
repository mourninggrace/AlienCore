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


# ── Features that require the base license ($20) ─────────────────────────────
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
    """
    if not auth.is_logged_in():
        return False, "Sign in to use this feature."
    if not auth.is_licensed():
        return False, "This feature requires AlienCore ($20 one-time purchase)."
    if feature in PRO_FEATURES and not auth.is_pro():
        return False, "This is a Pro feature. Upgrade with the Pro add-on (+$5)."
    return True, None


def is_allowed(feature: str) -> bool:
    ok, _ = check(feature)
    return ok


def require(feature: str):
    """Raise LicenseError if the feature isn't accessible."""
    ok, reason = check(feature)
    if not ok:
        raise LicenseError(reason)
