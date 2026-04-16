"""
AlienCore - fingerprint.py
Hardware fingerprint for trial abuse prevention.

Combines three identifiers that survive software reinstalls:
  1. Windows MachineGuid  — set at Windows install time (HKLM\\...\\Cryptography)
  2. BIOS UUID            — burned into firmware by the OEM (wmic csproduct)
  3. CPU processor name   — stable unless CPU is physically replaced

The three values are concatenated with '|' and SHA-256 hashed.  The first
32 hex chars are used as the fingerprint (128-bit collision resistance —
more than enough for this purpose).

If any individual source fails (permissions, WMI unavailable, virtualised
environment), the remaining sources still produce a useful fingerprint.
If ALL sources fail, returns the sentinel "unknown" and the caller handles
graceful degradation (trial allowed, email-only restriction applies).
"""

import hashlib
import logging
import subprocess
import winreg

logger = logging.getLogger("aliencore.fingerprint")

_CACHE: "str | None" = None


def get() -> str:
    """
    Return the machine fingerprint (cached after first call).
    Returns a 32-character hex string, or "unknown" if no sources succeeded.
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    _CACHE = _build()
    return _CACHE


def _build() -> str:
    parts = []

    # ── 1. Windows MachineGuid ────────────────────────────────────────────────
    # Written once by the Windows installer; persists across software changes.
    # Only changes if Windows is reinstalled from scratch.
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
        ) as k:
            guid, _ = winreg.QueryValueEx(k, "MachineGuid")
            if guid and len(guid) > 8:
                parts.append(f"mguid:{guid.strip().lower()}")
    except Exception as e:
        logger.debug("Fingerprint: MachineGuid unavailable — %s", e)

    # ── 2. BIOS / system UUID ─────────────────────────────────────────────────
    # Burned into firmware by the OEM (Dell/Alienware in this case).
    # Survives Windows reinstall and hard drive replacement.
    try:
        out = subprocess.check_output(
            ["wmic", "csproduct", "get", "UUID", "/value"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=6,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        for line in out.splitlines():
            line = line.strip()
            if line.upper().startswith("UUID="):
                val = line.split("=", 1)[1].strip().upper()
                # Skip the "all F's" placeholder that some BIOSes use
                if val and val != "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF":
                    parts.append(f"bios:{val}")
                break
    except Exception as e:
        logger.debug("Fingerprint: BIOS UUID unavailable — %s", e)

    # ── 3. CPU processor name ─────────────────────────────────────────────────
    # Stable across OS reinstalls; only changes if the CPU is physically swapped.
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        ) as k:
            name, _ = winreg.QueryValueEx(k, "ProcessorNameString")
            if name:
                parts.append(f"cpu:{name.strip()}")
    except Exception as e:
        logger.debug("Fingerprint: CPU name unavailable — %s", e)

    if not parts:
        logger.warning("Fingerprint: all sources failed — returning 'unknown'")
        return "unknown"

    raw = "|".join(parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    logger.debug("Fingerprint: %s  (sources: %d)", digest, len(parts))
    return digest
