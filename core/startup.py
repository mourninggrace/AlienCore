"""
AlienCore - startup.py
Manages the "Start with Windows" entry.

Two mechanisms, auto-selected:
  1. Task Scheduler (preferred) — /RL HIGHEST runs silently elevated at logon.
     Requires admin rights to install, but once installed every boot starts
     AlienCore with full permissions — no UAC prompt, no '---' sensors.
  2. HKCU Run registry (fallback) — no admin needed, but the resulting process
     has no admin rights.  CPU temp / DIMM / NVMe readings will show '---'.

enable() installs the elevated task if possible; otherwise writes the HKCU
Run entry pointing at launch.vbs.  disable() removes whichever one exists.
"""
# copykitten

import os
import sys
import winreg
import logging

from core import elevation

logger = logging.getLogger("aliencore.startup")

_REG_KEY  = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
_REG_NAME = "AlienCore"


def _launch_command() -> str:
    """Return the command that should be in the Run key (non-admin fallback)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    vbs  = os.path.join(base, "launch.vbs")
    return f'wscript.exe "{vbs}"'


def _write_vbs() -> bool:
    """Write launch.vbs next to aliencore.py with the current Python executable path."""
    base   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(base, "aliencore.py")
    vbs    = os.path.join(base, "launch.vbs")

    # Prefer pythonw.exe so no console window appears at startup
    python_dir = os.path.dirname(sys.executable)
    pythonw    = os.path.join(python_dir, "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable

    # The non-admin HKCU fallback must skip auto-elevation — otherwise every
    # boot pops a UAC prompt the user can't easily accept from the logon flow.
    content = (
        'Set oShell = CreateObject("WScript.Shell")\n'
        f'oShell.CurrentDirectory = "{base}"\n'
        f'oShell.Run """{pythonw}"" ""{script}"" --no-elevate", 0, False\n'
    )
    try:
        with open(vbs, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info("launch.vbs written: %s", vbs)
        return True
    except Exception as e:
        logger.error("Failed to write launch.vbs: %s", e)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Status
# ─────────────────────────────────────────────────────────────────────────────

def is_enabled() -> bool:
    """True if AlienCore is configured to auto-start via either mechanism."""
    return elevation.task_exists() or _reg_entry_exists()


def startup_mode() -> str:
    """Return 'task' | 'registry' | 'none' — which mechanism is active."""
    if elevation.task_exists():
        return "task"
    if _reg_entry_exists():
        return "registry"
    return "none"


def _reg_entry_exists() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as key:
            winreg.QueryValueEx(key, _REG_NAME)
            return True
    except OSError:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Enable / disable
# ─────────────────────────────────────────────────────────────────────────────

def enable() -> bool:
    """
    Install the preferred auto-start mechanism.

    - If running as admin, install the elevated Task Scheduler entry so
      AlienCore launches silently with full permissions at every logon.
      Also removes any HKCU Run entry so the two don't both fire.
    - Otherwise fall back to the HKCU Run entry (non-admin).  Sensor readings
      that need kernel-level access will show '---' until the user
      re-enables from an elevated session.
    """
    if elevation.is_admin():
        if elevation.install_elevated_task():
            _remove_reg_entry()   # avoid double-launch
            return True
        logger.warning("Task Scheduler install failed — falling back to HKCU Run")

    if not getattr(sys, "frozen", False):
        _write_vbs()
    try:
        cmd = _launch_command()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY,
                            access=winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, _REG_NAME, 0, winreg.REG_SZ, cmd)
        logger.info("Start with Windows enabled (HKCU Run): %s", cmd)
        return True
    except Exception as e:
        logger.error("Failed to enable startup entry: %s", e)
        return False


def disable() -> bool:
    """Remove any auto-start entry (both mechanisms, idempotent)."""
    ok = True
    if elevation.task_exists():
        if elevation.is_admin():
            if not elevation.uninstall_elevated_task():
                ok = False
        else:
            logger.warning("Elevated task exists but we're not admin — "
                           "can't remove it from this session.")
            ok = False
    _remove_reg_entry()
    return ok


def _remove_reg_entry() -> bool:
    """Remove HKCU Run entry if present. Returns True on success (including already absent)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY,
                            access=winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, _REG_NAME)
        logger.info("HKCU Run entry removed.")
        return True
    except FileNotFoundError:
        return True
    except Exception as e:
        logger.error("Failed to remove HKCU Run entry: %s", e)
        return False


def sync(enabled: bool):
    """Enable or disable to match the given boolean — used at startup."""
    if not enabled:
        disable()
        return

    mode = startup_mode()
    if mode == "none":
        enable()
        return
    if mode == "registry" and elevation.is_admin():
        # Opportunistic upgrade: we're now admin, so replace the non-admin
        # HKCU Run entry with a silent elevated Task Scheduler entry.
        logger.info("Upgrading HKCU Run startup entry to Task Scheduler (elevated).")
        enable()
        return
    if mode == "registry":
        # Still non-admin: ensure launch.vbs is current (older versions didn't
        # pass --no-elevate and would trigger a UAC prompt at every boot).
        if not getattr(sys, "frozen", False):
            _write_vbs()
