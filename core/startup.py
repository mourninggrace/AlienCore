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
        # Compiled exe — point straight at it
        return f'"{sys.executable}"'
    # Script mode — use the VBS launcher to avoid a visible console window
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    vbs  = os.path.join(base, "launch.vbs")
    return f'wscript.exe "{vbs}"'


def is_enabled() -> bool:
    """Return True if an AlienCore Run entry exists in the registry."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as key:
            winreg.QueryValueEx(key, _REG_NAME)
            return True
    except OSError:
        return False


def enable() -> bool:
    """Write the Run key entry. Returns True on success."""
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
