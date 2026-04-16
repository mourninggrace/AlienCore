"""
AlienCore - startup.py
Manages the Windows "Start with Windows" registry entry.

Uses HKCU Run key — no admin rights required.
Launch command:
  - Script mode  : wscript.exe "C:\\Aliencore\\launch.vbs"  (silent, no console)
  - Compiled exe : path to the exe directly
"""

import os
import sys
import winreg
import logging

logger = logging.getLogger("aliencore.startup")

_REG_KEY  = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
_REG_NAME = "AlienCore"


def _launch_command() -> str:
    """Return the command that should be in the Run key."""
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

    # VBS: run pythonw silently, cwd set to aliencore root
    content = (
        'Set oShell = CreateObject("WScript.Shell")\n'
        f'oShell.CurrentDirectory = "{base}"\n'
        f'oShell.Run """{pythonw}"" ""{script}""", 0, False\n'
    )
    try:
        with open(vbs, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info("launch.vbs written: %s", vbs)
        return True
    except Exception as e:
        logger.error("Failed to write launch.vbs: %s", e)
        return False


def is_enabled() -> bool:
    """Return True if an AlienCore Run entry exists in the registry."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as key:
            winreg.QueryValueEx(key, _REG_NAME)
            return True
    except OSError:
        return False


def enable() -> bool:
    """Write launch.vbs and the Run key entry. Returns True on success."""
    if not getattr(sys, "frozen", False):
        _write_vbs()   # ensure the VBS target exists before the registry points to it
    try:
        cmd = _launch_command()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY,
                            access=winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, _REG_NAME, 0, winreg.REG_SZ, cmd)
        logger.info("Start with Windows enabled: %s", cmd)
        return True
    except Exception as e:
        logger.error("Failed to enable startup entry: %s", e)
        return False


def disable() -> bool:
    """Remove the Run key entry. Returns True on success (including already absent)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY,
                            access=winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, _REG_NAME)
        logger.info("Start with Windows disabled.")
        return True
    except FileNotFoundError:
        return True   # already gone — that's fine
    except Exception as e:
        logger.error("Failed to disable startup entry: %s", e)
        return False


def sync(enabled: bool):
    """Enable or disable to match the given boolean — used at startup."""
    if enabled:
        enable()
    else:
        disable()
