"""
AlienCore - mem_tier.py

One-stop helper for scaling in-memory caches and history buffers to the
host's installed RAM.  The rest of the app treats tiers as an integer
multiplier hint:

    tier_idx()      -> 0..3           (low / normal / high / xl)
    multiplier()    -> 1, 1, 2, 4     (history / cache sizes)
    stale_seconds() -> seconds        (serve-stale cache window)

Rationale: AlienCore's own footprint is tiny (< 100 MB at rest).  On a
64 GB gaming rig, keeping 4x the sparkline history or holding a stale
LHM cache for 2 minutes is invisible; on an 8 GB ultrabook it matters.
Call sites scale their buffers once at startup off these values.

All values are cached for the lifetime of the process — installed RAM
doesn't change at runtime.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("aliencore.mem_tier")

_cached_total_gb: float | None = None
_cached_tier: int | None = None


def _total_gb() -> float:
    global _cached_total_gb
    if _cached_total_gb is not None:
        return _cached_total_gb
    try:
        import psutil
        _cached_total_gb = psutil.virtual_memory().total / (1024 ** 3)
    except Exception as e:
        logger.debug("mem_tier: psutil unavailable, assuming 8 GB (%s)", e)
        _cached_total_gb = 8.0
    return _cached_total_gb


def tier_idx() -> int:
    """
    0 = low    (<=  8 GB)
    1 = normal (<= 16 GB)
    2 = high   (<= 32 GB)
    3 = xl     (>  32 GB)
    """
    global _cached_tier
    if _cached_tier is not None:
        return _cached_tier
    g = _total_gb()
    if   g <=  8.5: _cached_tier = 0
    elif g <= 16.5: _cached_tier = 1
    elif g <= 32.5: _cached_tier = 2
    else:           _cached_tier = 3
    return _cached_tier


def multiplier() -> int:
    """Scale factor for history/buffer sizes."""
    return (1, 1, 2, 4)[tier_idx()]


def stale_seconds(base: float) -> float:
    """Scale a 'serve stale cache' window by RAM tier."""
    return base * (1, 1, 2, 4)[tier_idx()]


def scaled(base: int, *, cap: int | None = None) -> int:
    """Scale a default buffer length by multiplier(); optionally cap."""
    n = base * multiplier()
    return min(n, cap) if cap is not None else n


def describe() -> str:
    names = ("low", "normal", "high", "xl")
    return f"{names[tier_idx()]} ({_total_gb():.1f} GB, x{multiplier()})"
