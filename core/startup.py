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


def _safe_path_for_template(p: str) -> bool:
    """A path is safe to interpolate into the VBS / registry template only if
    it contains no characters that would break VBScript string parsing or let
    a crafted install path inject extra commands.  Refuses paths with ", ',
    \\r, \\n, or NUL — every other Windows-legal path character is fine."""
    if not isinstance(p, str) or not p:
        return False
    return not any(c in p for c in ('"', "'", "\r", "\n", "\x00"))


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

    # Refuse to template a VBS with paths that would break — or let an
    # attacker who controls the install location inject — VBScript syntax.
    for label, p in (("base", base), ("pythonw", pythonw), ("script", script)):
        if not _safe_path_for_template(p):
            logger.error(
                "launch.vbs not written: %s path contains unsafe characters: %r",
                label, p,
            )
            return False

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
        if not _write_vbs():
            return False
    try:
        cmd = _launch_command()
        # Defense-in-depth: refuse to register a Run entry whose command
        # contains characters that could be re-interpreted by the shell
        # parser when the registry value is later expanded.
        if any(c in cmd for c in ("\r", "\n", "\x00")):
            logger.error("Refusing HKCU Run entry — command contains control chars")
            return False
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
    # Always clean up stale tasks from older AlienCore versions.  A left-over
    # `AlienCore` task alongside the current `AlienCoreElevatedStartup` task
    # causes duplicate launches at logon (mutex catches the duplicate, but both
    # interpreters still pay the full import cost — ~30 s slower login).
    elevation.cleanup_legacy_tasks()

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
