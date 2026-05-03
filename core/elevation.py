"""
AlienCore - elevation.py
Detects admin rights and provides UAC elevation helpers.

Why this matters:
  Without admin rights, LHM can't load WinRing0.sys (CPU MSR temps), SMBus is
  blocked (DIMM temps), and AWCC WMI returns WBEM_E_ACCESS_DENIED (0x80041003).
  Result: the sensor bar shows '---' for CPU temp, DIMM, and NVMe readings.

  Auto-starting via HKCU Run gives a non-admin process.  This module lets us
  relaunch elevated (UAC prompt) or use Task Scheduler for silent elevation.
"""
# copykitten

import ctypes
import logging
import os
import subprocess
import sys

from core.constants import BASE_DIR as _BASE_DIR

logger = logging.getLogger("aliencore.elevation")

_TASK_NAME = "AlienCoreElevatedStartup"

# Historical task names installed by earlier versions of startup.py.  If any of
# these still exist they will fire at logon alongside _TASK_NAME, producing
# duplicate processes (the mutex catches it, but both run their full Python
# import cycle and slow login by ~30 s).
_LEGACY_TASK_NAMES = ("AlienCore",)


# ─────────────────────────────────────────────────────────────────────────────
# Admin detection
# ─────────────────────────────────────────────────────────────────────────────

def is_admin() -> bool:
    """Return True if the current process has administrator rights."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# UAC relaunch
# ─────────────────────────────────────────────────────────────────────────────

def relaunch_as_admin(extra_args: list[str] | None = None) -> bool:
    """
    Relaunch the current process with administrator rights via UAC prompt.

    Returns True if the elevated instance was successfully spawned (caller
    should exit); False if the user declined UAC or the call failed.
    The original (non-elevated) process should sys.exit(0) on success so
    only the elevated copy continues.
    """
    if is_admin():
        return True   # already elevated

    script = os.path.abspath(sys.argv[0])
    params = [script] + (sys.argv[1:] if len(sys.argv) > 1 else [])
    if extra_args:
        params.extend(extra_args)

    # Refuse to forward args containing control characters — these can't
    # appear in a legitimate path or argparse flag and would only be present
    # if argv was crafted to break out of subprocess.list2cmdline's quoting.
    for p in params:
        if not isinstance(p, str) or any(c in p for c in ("\r", "\n", "\x00")):
            logger.error("relaunch_as_admin refused — argv contains control chars")
            return False

    # subprocess.list2cmdline implements the exact escaping that
    # CommandLineToArgvW expects on the receiving side, so embedded quotes
    # and backslash-runs round-trip cleanly.
    quoted = subprocess.list2cmdline(params)

    try:
        # SW_SHOWNORMAL = 1
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, quoted, _BASE_DIR, 1
        )
        # ShellExecuteW returns > 32 on success
        if ret > 32:
            logger.info("Elevated instance spawned (ShellExecuteW returned %d)", ret)
            return True
        # 5 = SE_ERR_ACCESSDENIED (user declined UAC)
        logger.warning("Elevation declined or failed (ShellExecuteW returned %d)", ret)
        return False
    except Exception as e:
        logger.error("Elevation failed: %s", e)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Task Scheduler — silent elevated auto-start
# ─────────────────────────────────────────────────────────────────────────────

def _launch_target() -> tuple[str, str]:
    """Return (executable, arguments) that should be invoked at startup."""
    if getattr(sys, "frozen", False):
        return sys.executable, ""

    python_dir = os.path.dirname(sys.executable)
    pythonw    = os.path.join(python_dir, "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    script = os.path.join(_BASE_DIR, "aliencore.py")
    return pythonw, f'"{script}"'


def task_exists() -> bool:
    """Return True if the elevated startup Task Scheduler entry exists."""
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", _TASK_NAME],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _path_safe_for_elevated_task(p: str) -> bool:
    """Reject install paths a non-admin user could overwrite, since the task
    runs /RL HIGHEST.  A non-admin who can swap aliencore.py / python.exe gains
    persistent elevated execution on every login.

    'Safe' means the path lives under one of the system program directories
    (Program Files / Program Files (x86) / System32).  Per-user Python installs
    in %LOCALAPPDATA%\\Programs\\Python and project trees on Desktop or in the
    user profile are all rejected.

    Set ALIENCORE_ALLOW_USER_INSTALL=1 in the environment to bypass during
    development — never set this in production builds.
    """
    if os.environ.get("ALIENCORE_ALLOW_USER_INSTALL") == "1":
        return True
    try:
        norm = os.path.normcase(os.path.abspath(p))
    except Exception:
        return False
    pf   = os.path.normcase(os.environ.get("ProgramFiles", r"C:\Program Files"))
    pf86 = os.path.normcase(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    sysroot = os.path.normcase(os.environ.get("SystemRoot", r"C:\Windows"))
    safe_roots = [pf, pf86, os.path.join(sysroot, "system32")]
    for root in safe_roots:
        try:
            if os.path.commonpath([norm, root]) == root:
                return True
        except ValueError:
            # different drives — commonpath raises
            continue
    return False


def _path_safe_for_template(p: str) -> bool:
    """A path that flows into the schtasks /TR string must not contain
    characters that would break command-line parsing."""
    return isinstance(p, str) and bool(p) and not any(
        c in p for c in ('"', "'", "\r", "\n", "\x00")
    )


def install_elevated_task() -> bool:
    """
    Create a Task Scheduler entry that runs AlienCore at login with highest
    privileges (no UAC prompt at boot).  Requires admin rights to install.
    """
    if not is_admin():
        logger.warning("install_elevated_task called without admin rights")
        return False

    exe, args = _launch_target()
    script = os.path.join(_BASE_DIR, "aliencore.py")

    # Refuse if either the interpreter or the install directory is in a
    # location a non-admin can write to.  Otherwise the elevated task
    # becomes a persistent local privilege escalation primitive.
    for label, p in (("interpreter", exe),
                     ("install_dir", _BASE_DIR),
                     ("script", script)):
        if not _path_safe_for_elevated_task(p):
            logger.error(
                "Refusing to install elevated startup task — %s lives in a "
                "user-writable location: %r. Move AlienCore (and its Python "
                "interpreter) to Program Files, or set "
                "ALIENCORE_ALLOW_USER_INSTALL=1 if you accept the LPE risk "
                "(development only).",
                label, p,
            )
            return False
        if not _path_safe_for_template(p):
            logger.error(
                "Refusing to install elevated startup task — %s path "
                "contains unsafe characters: %r", label, p,
            )
            return False

    tr = f'"{exe}" {args}'.strip()

    # /RL HIGHEST = run with highest privileges
    # /SC ONLOGON = trigger at user logon
    # /IT = interactive (so tray icon and GUI appear)
    # /F = overwrite existing task
    cmd = [
        "schtasks", "/Create",
        "/TN", _TASK_NAME,
        "/TR", tr,
        "/SC", "ONLOGON",
        "/RL", "HIGHEST",
        "/IT",
        "/F",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=15,
        )
        if result.returncode == 0:
            logger.info("Elevated startup task installed: %s", _TASK_NAME)
            return True
        logger.error("schtasks create failed: %s", result.stderr.strip())
        return False
    except Exception as e:
        logger.error("install_elevated_task error: %s", e)
        return False


def uninstall_elevated_task() -> bool:
    """Remove the Task Scheduler entry.  Requires admin rights."""
    if not is_admin():
        logger.warning("uninstall_elevated_task called without admin rights")
        return False
    try:
        result = subprocess.run(
            ["schtasks", "/Delete", "/TN", _TASK_NAME, "/F"],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=10,
        )
        if result.returncode == 0:
            logger.info("Elevated startup task removed.")
            return True
        # Task doesn't exist — that's fine
        if "cannot find" in result.stderr.lower() or "does not exist" in result.stderr.lower():
            return True
        logger.error("schtasks delete failed: %s", result.stderr.strip())
        return False
    except Exception as e:
        logger.error("uninstall_elevated_task error: %s", e)
        return False


def cleanup_legacy_tasks() -> int:
    """Remove any stale Task Scheduler entries from earlier AlienCore versions.

    Returns the number of legacy tasks that were actually removed.  Requires
    admin rights; silently no-ops otherwise.
    """
    if not is_admin():
        return 0
    removed = 0
    for name in _LEGACY_TASK_NAMES:
        try:
            query = subprocess.run(
                ["schtasks", "/Query", "/TN", name],
                capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=10,
            )
            if query.returncode != 0:
                continue   # doesn't exist — nothing to do
            result = subprocess.run(
                ["schtasks", "/Delete", "/TN", name, "/F"],
                capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=10,
            )
            if result.returncode == 0:
                logger.info("Removed legacy startup task: %s", name)
                removed += 1
            else:
                logger.warning("Failed to remove legacy task %s: %s",
                               name, result.stderr.strip())
        except Exception as e:
            logger.warning("cleanup_legacy_tasks(%s) error: %s", name, e)
    return removed
