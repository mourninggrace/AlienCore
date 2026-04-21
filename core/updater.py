"""
AlienCore - updater.py
Checks GitHub releases for newer versions and manages the update lifecycle.

Check flow:
  1. start_background_check() spawns a daemon thread.
  2. Thread waits UPDATE_CHECK_DELAY seconds (avoids slowing startup), then
     fetches the GitHub releases API and stores the result in _cached.
  3. If a newer version is found and not suppressed, _dialog_pending is set.
  4. The tray update loop detects _dialog_pending, fires a toast, and spawns
     a thread to show the update dialog.

State file (update_state.json in BASE_DIR):
  remind_after      : float | null  — unix timestamp; suppress dialog until then
  dismissed_version : str   | null  — suppress dialog for this version;
                                      show the settings footer button instead
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile

from core.constants import APP_NAME, BASE_DIR, VERSION

logger = logging.getLogger("aliencore.updater")

GITHUB_REPO             = "mourninggrace/AlienCore"
GITHUB_API_URL          = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
STATE_PATH              = os.path.join(BASE_DIR, "update_state.json")
UPDATE_CHECK_DELAY      = 30       # seconds after startup before first check
UPDATE_RECHECK_HOURS    = 6        # hours between subsequent checks

_lock           = threading.Lock()
_cached: dict   = {}               # {} = no update / not yet checked; {...} = update info
_checked        = False            # True once first check completes
_dialog_pending = threading.Event()


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def start_background_check():
    """Spawn a daemon thread that checks GitHub for updates on a recurring schedule."""
    t = threading.Thread(target=_check_loop, name="UpdateChecker", daemon=True)
    t.start()


def get_update_info() -> "dict | None":
    """
    Return update info dict if a newer version is available, else None.
    Returns None if the check hasn't completed yet or no update exists.
    Keys: version, notes, zipball_url, html_url
    """
    with _lock:
        if _cached.get("version"):
            return dict(_cached)
        return None


def has_checked() -> bool:
    """True once the first network check has completed (success or failure)."""
    with _lock:
        return _checked


def should_show_dialog() -> bool:
    """True if an update is available and the dialog hasn't been suppressed."""
    info = get_update_info()
    if not info:
        return False
    state = _load_state()
    remind_after = state.get("remind_after")
    if remind_after and time.time() < remind_after:
        return False
    if state.get("dismissed_version") == info["version"]:
        return False
    return True


def should_show_button() -> bool:
    """True if the user clicked 'Not Now' — show settings button, not dialog."""
    info = get_update_info()
    if not info:
        return False
    state = _load_state()
    return state.get("dismissed_version") == info["version"]


def is_dialog_pending() -> bool:
    return _dialog_pending.is_set()


def clear_dialog_pending():
    _dialog_pending.clear()


def set_remind_later():
    """Suppress the update dialog for 24 hours."""
    state = _load_state()
    state["remind_after"]      = time.time() + 86400
    state["dismissed_version"] = None
    _save_state(state)
    logger.info("Update reminder snoozed for 24 hours.")


def set_dismissed(version: str):
    """User clicked 'Not Now' — suppress dialog, show settings button instead."""
    state = _load_state()
    state["dismissed_version"] = version
    state["remind_after"]      = None
    _save_state(state)
    logger.info("Update dismissed for v%s (button shown in settings).", version)


def clear_state():
    """Clear update-suppression flags — used after a successful update.

    Preserves other keys (notably last_seen_version used by whats_new_dialog)
    so the post-update restart still shows the release notes."""
    state = _load_state()
    state["remind_after"]      = None
    state["dismissed_version"] = None
    _save_state(state)


def download_and_apply(zipball_url: str, on_progress=None):
    """
    Download the release zip, extract it, write a batch helper that copies
    files over the current installation and restarts AlienCore, then launch
    the helper and exit this process.

    on_progress(pct: int, msg: str) is called with progress updates if provided.
    Raises RuntimeError on any fatal error so the caller can display the message.
    """
    app_dir  = BASE_DIR
    tmp_dir  = tempfile.mkdtemp(prefix="aliencore_update_")
    zip_path = os.path.join(tmp_dir, "update.zip")

    # ── Download ──
    logger.info("Downloading update: %s", zipball_url)
    _progress(on_progress, 0, "Downloading update...")
    try:
        req = urllib.request.Request(
            zipball_url,
            headers={"User-Agent": f"{APP_NAME}/{VERSION}"}
        )
        with urllib.request.urlopen(req, timeout=120) as resp, \
             open(zip_path, "wb") as fout:
            total      = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                fout.write(chunk)
                downloaded += len(chunk)
                if total:
                    _progress(on_progress,
                              int(downloaded / total * 55),
                              f"Downloading... {downloaded//1024} KB")
    except Exception as e:
        import shutil
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass
        raise RuntimeError(f"Download failed: {e}") from e

    # ── Extract ──
    _progress(on_progress, 60, "Extracting...")
    extract_dir = os.path.join(tmp_dir, "extracted")
    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(extract_dir)
    except Exception as e:
        raise RuntimeError(f"Extraction failed: {e}") from e

    # GitHub zips have a single top-level subdirectory (e.g. "owner-repo-sha/").
    entries = [e for e in os.listdir(extract_dir)
               if os.path.isdir(os.path.join(extract_dir, e))]
    content_root = (os.path.join(extract_dir, entries[0])
                    if len(entries) == 1 else extract_dir)

    _progress(on_progress, 75, "Preparing update launcher...")

    # ── Write the helper batch script ──
    python_exe = sys.executable
    main_py    = os.path.join(app_dir, "aliencore.py")
    bat_path   = os.path.join(tmp_dir, "do_update.bat")

    # robocopy exit codes: 0 = no files copied, 1 = files copied, 2-7 = warnings,
    # 8+ = errors.  We only fail if exit code >= 8.
    bat_lines = [
        "@echo off",
        "chcp 65001 >nul",
        "timeout /t 2 /nobreak >nul",
        f'robocopy "{content_root}" "{app_dir}" /E /XO /XF do_update.bat /NFL /NDL /NJH /NJS',
        "if %errorlevel% geq 8 (",
        "    echo Update copy failed >&2",
        "    exit /b 1",
        ")",
        f'start "" "{python_exe}" "{main_py}"',
    ]
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write("\r\n".join(bat_lines) + "\r\n")

    _progress(on_progress, 90, "Launching update — restarting AlienCore...")
    logger.info("Launching update bat: %s", bat_path)

    subprocess.Popen(
        ["cmd.exe", "/c", bat_path],
        creationflags=(subprocess.CREATE_NO_WINDOW
                       | subprocess.DETACHED_PROCESS),
        close_fds=True,
    )

    clear_state()
    logger.info("Update launched — exiting current process.")
    time.sleep(0.5)
    sys.exit(0)


# ─────────────────────────────────────────────────────────────────────────────
# Internal
# ─────────────────────────────────────────────────────────────────────────────

def _progress(cb, pct: int, msg: str):
    if cb:
        try:
            cb(pct, msg)
        except Exception:
            pass


def _check_loop():
    global _checked
    time.sleep(UPDATE_CHECK_DELAY)
    while True:
        try:
            _do_check()
        except Exception as e:
            logger.debug("Update check failed: %s", e)
        finally:
            with _lock:
                _checked = True
        time.sleep(UPDATE_RECHECK_HOURS * 3600)


def _do_check():
    req = urllib.request.Request(
        GITHUB_API_URL,
        headers={
            "User-Agent": f"{APP_NAME}/{VERSION}",
            "Accept":     "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())

    tag = (data.get("tag_name") or "").lstrip("v").strip()
    if not tag:
        logger.debug("GitHub release has no tag — skipping.")
        return

    if _version_tuple(tag) > _version_tuple(VERSION):
        body  = (data.get("body") or "").strip()
        notes = "\n".join(body.splitlines()[:10])
        info  = {
            "version":     tag,
            "notes":       notes,
            "zipball_url": data.get("zipball_url", ""),
            "html_url":    data.get("html_url", ""),
        }
        with _lock:
            _cached.clear()
            _cached.update(info)
        logger.info("Update available: v%s  (current: v%s)", tag, VERSION)
        if should_show_dialog():
            _dialog_pending.set()
    else:
        with _lock:
            _cached.clear()   # no update
        logger.debug("AlienCore is up to date (v%s).", VERSION)


def _version_tuple(v: str) -> tuple:
    try:
        return tuple(int(x) for x in str(v).split("."))
    except (ValueError, AttributeError):
        return (0,)


def _load_state() -> dict:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"remind_after": None, "dismissed_version": None}


def _save_state(state: dict):
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.debug("Failed to save update state: %s", e)
