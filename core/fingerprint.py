"""
AlienCore - fingerprint.py
Hardware fingerprint for trial abuse prevention.

Combines three identifiers that survive software reinstalls:
  1. Windows MachineGuid  — set at Windows install time (HKLM\\...\\Cryptography)
  2. BIOS UUID            — burned into firmware by the OEM (Get-CimInstance,
                            falling back to absolute-path wmic.exe)
  3. CPU processor name   — stable unless CPU is physically replaced

The three values are concatenated with '|' and SHA-256 hashed.  The first
32 hex chars are used as the fingerprint (128-bit collision resistance —
more than enough for this purpose).

If any individual source fails (permissions, WMI unavailable, virtualised
environment), the remaining sources still produce a useful fingerprint.
If ALL sources fail, returns the sentinel "unknown".  The server rejects
"unknown" at /auth/verify-pin, and `is_resolved()` lets clients gate startup.

External tools are invoked via absolute paths under %SystemRoot%\\System32
to defeat PATH-hijack attacks (a user-writable directory ahead of System32
in PATH could otherwise host a poisoned wmic.exe / powershell.exe that
returns whatever fingerprint the attacker chose, bypassing trial-burn).
"""

import hashlib
import logging
import os
import subprocess
import winreg

logger = logging.getLogger("aliencore.fingerprint")

_CACHE: "str | None" = None
UNKNOWN = "unknown"


def _system32_path(*parts: str) -> str:
    """Build an absolute path under %SystemRoot%\\System32 (PATH-hijack-safe)."""
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    return os.path.join(root, "System32", *parts)


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


def is_resolved() -> bool:
    """True if the fingerprint identifies real hardware (not the unknown sentinel)."""
    return get() != UNKNOWN


def _read_bios_uuid() -> str:
    """Try PowerShell Get-CimInstance first (works on Win11 24H2 where WMIC is
    being removed), then absolute-path wmic.exe as a fallback."""
    pwsh = _system32_path("WindowsPowerShell", "v1.0", "powershell.exe")
    if os.path.exists(pwsh):
        try:
            out = subprocess.check_output(
                [pwsh, "-NoProfile", "-NonInteractive", "-Command",
                 "(Get-CimInstance Win32_ComputerSystemProduct).UUID"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            val = out.strip().upper()
            if val and val != "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF":
                return val
        except Exception as e:
            logger.debug("Fingerprint: PowerShell UUID query failed — %s", e)

    wmic = _system32_path("wbem", "wmic.exe")
    if os.path.exists(wmic):
        try:
            out = subprocess.check_output(
                [wmic, "csproduct", "get", "UUID", "/value"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=6,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            for line in out.splitlines():
                line = line.strip()
                if line.upper().startswith("UUID="):
                    val = line.split("=", 1)[1].strip().upper()
                    if val and val != "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF":
                        return val
        except Exception as e:
            logger.debug("Fingerprint: wmic UUID query failed — %s", e)
    return ""


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
    bios = _read_bios_uuid()
    if bios:
        parts.append(f"bios:{bios}")

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
        return UNKNOWN

    raw = "|".join(parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    logger.debug("Fingerprint: %s  (sources: %d)", digest, len(parts))
    return digest
