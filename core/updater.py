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

import base64
import hashlib
import json
import logging
import os
import re
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.parse
import zipfile

from core.constants import APP_NAME, BASE_DIR, USER_DATA_DIR, VERSION
from core.update_pubkey import UPDATE_PUBLIC_KEY_B64

logger = logging.getLogger("aliencore.updater")

GITHUB_REPO             = "mourninggrace/AlienCore"
GITHUB_API_URL          = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
STATE_PATH              = os.path.join(USER_DATA_DIR, "update_state.json")
UPDATE_CHECK_DELAY      = 30       # seconds after startup before first check
UPDATE_RECHECK_HOURS    = 6        # hours between subsequent checks

_lock           = threading.Lock()
_cached: dict   = {}               # {} = no update / not yet checked; {...} = update info
_checked        = False            # True once first check completes
_dialog_pending = threading.Event()

# Hostnames the updater is permitted to fetch from.  Anything else (a
# zipball_url / html_url that an attacker rewrote to point at their own
# server) is rejected before urlopen() ever runs.
_ALLOWED_HOST_SUFFIXES = ("github.com", "githubusercontent.com", "api.github.com")


def _ssl_context() -> ssl.SSLContext:
    """Explicit default TLS context (cert + hostname verification on) used for
    every network fetch, rather than relying on urlopen's implicit default.
    Defence-in-depth alongside the Ed25519 signature gate."""
    return ssl.create_default_context()


def _validate_https_github(url: str) -> str:
    """Reject any URL that is not https:// on a GitHub-owned host.

    Returns the url unchanged if valid; raises RuntimeError otherwise.  This
    runs BEFORE urlopen() for the release check, the zipball download, and any
    other URL the updater opens, so a MITM/compromised API response cannot
    redirect the privileged downloader at an attacker-controlled host."""
    if not isinstance(url, str) or not url:
        raise RuntimeError("Refusing to fetch an empty/invalid update URL.")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise RuntimeError(f"Refusing non-HTTPS update URL: {url!r}")
    host = (parsed.hostname or "").lower()
    if not host or not any(
        host == suf or host.endswith("." + suf) for suf in _ALLOWED_HOST_SUFFIXES
    ):
        raise RuntimeError(f"Refusing update URL on untrusted host {host!r}: {url!r}")
    return url


def _load_update_public_key():
    """Decode the embedded base64 update-signing public key.

    Returns (Ed25519PublicKey, None) on success, or (None, error_msg) so the
    caller can fail closed.  A blank/placeholder key returns (None, msg)."""
    if not UPDATE_PUBLIC_KEY_B64 or all(c in ("A", "=") for c in UPDATE_PUBLIC_KEY_B64):
        return None, "Update public key is not configured (placeholder)"
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        raw = base64.b64decode(UPDATE_PUBLIC_KEY_B64, validate=True)
        if len(raw) != 32:
            return None, f"Update public key wrong length: {len(raw)}"
        return Ed25519PublicKey.from_public_bytes(raw), None
    except ImportError:
        return None, "cryptography package missing"
    except Exception as e:
        return None, f"Update public key load failed: {e}"


def _verify_update_signature(zip_bytes: bytes, sig_b64: "str | None"):
    """Verify a detached Ed25519 signature (base64) over the raw zip bytes
    against the embedded update public key.

    Raises RuntimeError if the key is unconfigured, the signature is missing/
    malformed, or verification fails.  Returns None on success.  This is the
    TRUST ROOT for auto-updates — the SHA-256 is only a cheap pre-check."""
    key, err = _load_update_public_key()
    if key is None:
        raise RuntimeError(
            f"Cannot verify update signature — {err}. Refusing to apply update."
        )
    if not isinstance(sig_b64, str) or not sig_b64.strip():
        raise RuntimeError(
            "This release is not cryptographically signed (no update signature "
            "found). Refusing to apply — update manually from the GitHub release "
            "page if you trust it."
        )
    try:
        sig = base64.b64decode(sig_b64.strip(), validate=True)
    except Exception as e:
        raise RuntimeError(f"Update signature is malformed: {e}") from e
    if len(sig) != 64:
        raise RuntimeError(
            f"Update signature wrong length ({len(sig)} bytes) — refusing update."
        )
    try:
        key.verify(sig, zip_bytes)
    except Exception:
        raise RuntimeError(
            "Update signature verification FAILED — the package is not signed "
            "by the AlienCore release key and may be tampered. Aborting update."
        )
    logger.info("Update Ed25519 signature verified.")


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


RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"


def is_frozen() -> bool:
    """True if AlienCore is running from a PyInstaller-frozen build (the
    .exe shipped via the installer).  In that case the install dir is just
    aliencore.exe + _internal/ — there are no .py files for the in-app
    updater's robocopy step to overlay, so the auto-update path is disabled
    and the user is directed to download a fresh installer instead."""
    return getattr(sys, "frozen", False)


def download_and_apply(zipball_url: str, on_progress=None,
                       expected_sha256: "str | None" = None,
                       expected_version: "str | None" = None,
                       expected_sig: "str | None" = None):
    """
    Download the release zip, extract it, write a batch helper that copies
    files over the current installation and restarts AlienCore, then launch
    the helper and exit this process.

    on_progress(pct: int, msg: str) is called with progress updates if provided.
    Raises RuntimeError on any fatal error so the caller can display the message.

    expected_version: the tag this download is supposed to be — re-checked at
    apply time as a downgrade-prevention barrier.  An attacker who can MITM
    api.github.com (or who later gains release-edit rights) cannot downgrade
    a user past a security fix without also publishing a new tag whose name
    is greater than the current one.
    """
    # Frozen builds (PyInstaller .exe distributed via the Inno Setup
    # installer) cannot be auto-updated by overlaying source files — the
    # install dir is `aliencore.exe + _internal/`, not loose .py files.
    # Direct the user to download a fresh installer from GitHub instead.
    if is_frozen():
        raise RuntimeError(
            "Auto-update isn't available for installed builds.\n"
            f"Please download v{expected_version or 'the latest release'} "
            f"from {RELEASES_URL} and run the installer."
        )

    # Re-check at apply time, not just at the dialog.  download_and_apply is
    # the only privileged entry — defending it once defends every code path.
    if expected_version:
        if _version_tuple(expected_version) <= _version_tuple(VERSION):
            raise RuntimeError(
                f"Refusing to apply v{expected_version} — current is v{VERSION}. "
                "Downgrades are not allowed."
            )

    app_dir  = BASE_DIR

    # ── Install-location safety gate (LPE primitive defence) ──
    # The updater runs AS ADMIN and robocopies the new tree over the install
    # dir.  If BASE_DIR / the Python interpreter / the launch script live
    # somewhere a non-admin user can write (Desktop, %LOCALAPPDATA%, a project
    # checkout), then overlaying them as admin lets any local user stage files
    # that then run elevated — a local privilege-escalation primitive.  Reuse
    # the SAME install-location check the elevated-task installer uses and
    # refuse the auto-apply for unsafe locations.  ALIENCORE_ALLOW_USER_INSTALL=1
    # bypasses for development (handled inside _path_safe_for_elevated_task).
    from core.elevation import _path_safe_for_elevated_task
    main_py_check = os.path.join(app_dir, "aliencore.py")
    unsafe = [
        p for p in (app_dir, sys.executable, main_py_check)
        if not _path_safe_for_elevated_task(p)
    ]
    if unsafe:
        logger.warning(
            "Auto-update refused: install location is user-writable "
            "(unsafe paths: %s). Manual update required.", unsafe
        )
        raise RuntimeError(
            "Auto-update is disabled because AlienCore is installed in a "
            "user-writable location, where overlaying files as Administrator "
            "would be a privilege-escalation risk.\n"
            f"Please update manually from {RELEASES_URL}."
        )

    tmp_dir  = tempfile.mkdtemp(prefix="aliencore_update_")
    zip_path = os.path.join(tmp_dir, "update.zip")

    # ── Download ──
    _validate_https_github(zipball_url)
    logger.info("Downloading update: %s", zipball_url)
    _progress(on_progress, 0, "Downloading update...")
    try:
        req = urllib.request.Request(
            zipball_url,
            headers={"User-Agent": f"{APP_NAME}/{VERSION}"}
        )
        ctx = _ssl_context()
        with urllib.request.urlopen(req, timeout=120, context=ctx) as resp, \
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

    # ── Verify SHA-256 (cheap pre-check only — NOT the trust root) ──
    # The SHA-256 comes from the same GitHub response as the zip URL, so it
    # only catches accidental corruption / truncation.  The real trust anchor
    # is the Ed25519 signature verified below against an embedded public key.
    if expected_sha256:
        _progress(on_progress, 57, "Verifying integrity...")
        h = hashlib.sha256()
        with open(zip_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        if h.hexdigest() != expected_sha256:
            _cleanup_tmp(tmp_dir)
            raise RuntimeError("Integrity check failed — update package may be tampered. Aborting.")
        logger.info("Update SHA-256 verified (pre-check).")

    # ── Verify Ed25519 signature (TRUST ROOT) ──
    # This MUST pass before any extraction or robocopy.  An attacker who can
    # MITM / edit the GitHub release supplies both the zip and its SHA-256, but
    # cannot forge a signature without update_private.pem.  Missing or invalid
    # signature => abort and clean up.
    _progress(on_progress, 58, "Verifying signature...")
    try:
        with open(zip_path, "rb") as f:
            zip_bytes = f.read()
        # The GUI caller (gui/update_dialog.py) passes the cached release info
        # but not the signature explicitly; fall back to the `sig` parsed from
        # the release body in _do_check() so existing call sites stay valid.
        sig = expected_sig
        if sig is None:
            with _lock:
                sig = _cached.get("sig")
        _verify_update_signature(zip_bytes, sig)
    except RuntimeError:
        _cleanup_tmp(tmp_dir)
        raise

    # ── Extract (zip-slip safe) ──
    _progress(on_progress, 60, "Extracting...")
    extract_dir = os.path.abspath(os.path.join(tmp_dir, "extracted"))
    os.makedirs(extract_dir, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            for member in z.infolist():
                _safe_extract_member(z, member, extract_dir)
    except RuntimeError:
        # Path-traversal rejection — re-raise without wrapping so the
        # message reaches the user verbatim.
        raise
    except Exception as e:
        raise RuntimeError(f"Extraction failed: {e}") from e

    # GitHub zips have a single top-level subdirectory (e.g. "owner-repo-sha/").
    entries = [e for e in os.listdir(extract_dir)
               if os.path.isdir(os.path.join(extract_dir, e))]
    content_root = (os.path.join(extract_dir, entries[0])
                    if len(entries) == 1 else extract_dir)

    # ── Cross-check the bundled VERSION constant ──
    # Even with SHA-256 verification, a release whose body line points at
    # an older zipball (downgrade-by-pointer) would be caught here because
    # the embedded VERSION wouldn't match expected_version.
    if expected_version:
        try:
            with open(os.path.join(content_root, "core", "constants.py"),
                      "r", encoding="utf-8") as f:
                src = f.read()
            m = re.search(r'^VERSION\s*=\s*["\']([0-9.]+)["\']', src, re.MULTILINE)
            zipped = m.group(1) if m else None
            if not zipped or _version_tuple(zipped) != _version_tuple(expected_version):
                raise RuntimeError(
                    f"Update payload version mismatch — release claims v{expected_version} "
                    f"but bundled VERSION is v{zipped}. Aborting."
                )
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Could not verify update version: {e}") from e

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

def _safe_extract_member(z: zipfile.ZipFile, member: zipfile.ZipInfo, dest: str):
    """Extract a single zip member with full path-traversal validation.

    Rejects:
      · absolute paths (Windows or POSIX)
      · drive letters (`C:\\…`)
      · `..` segments that escape the destination
      · symlinks (rare in GitHub zips, but a CLR runtime could include them)

    Caller has already validated the SHA-256 of the archive, so this is
    defence-in-depth for the case where the trust root (GitHub release
    publisher) is compromised."""
    name = member.filename
    if not name or name.endswith("/"):
        # Empty entries / pure-directory entries: directory creation happens
        # implicitly when we write a file into it.
        if name:
            target = os.path.normpath(os.path.join(dest, name))
            if os.path.commonpath([os.path.abspath(target), dest]) == dest:
                os.makedirs(target, exist_ok=True)
        return
    # Reject absolute paths and drive letters before joining.
    if name.startswith("/") or name.startswith("\\") or (len(name) > 1 and name[1] == ":"):
        raise RuntimeError(f"Update package contains an absolute path: {name!r} — refusing to extract.")
    target = os.path.abspath(os.path.normpath(os.path.join(dest, name)))
    if os.path.commonpath([target, dest]) != dest:
        raise RuntimeError(f"Update package contains a path-traversal entry: {name!r} — refusing to extract.")
    # Symlinks: the high bit of external_attr indicates POSIX file type;
    # 0xA000 is S_IFLNK.  Skip without writing.
    mode = (member.external_attr >> 16) & 0xF000
    if mode == 0xA000:
        logger.warning("Update package contains a symlink (%s) — skipping.", name)
        return
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with z.open(member) as src, open(target, "wb") as dst:
        while True:
            chunk = src.read(65536)
            if not chunk:
                break
            dst.write(chunk)


def _cleanup_tmp(tmp_dir: str):
    """Best-effort removal of the temp download/extract dir on abort."""
    import shutil
    try:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass


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
    _validate_https_github(GITHUB_API_URL)
    req = urllib.request.Request(
        GITHUB_API_URL,
        headers={
            "User-Agent": f"{APP_NAME}/{VERSION}",
            "Accept":     "application/vnd.github+json",
        },
    )
    ctx = _ssl_context()
    with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
        data = json.loads(resp.read().decode())

    tag = (data.get("tag_name") or "").lstrip("v").strip()
    if not tag:
        logger.debug("GitHub release has no tag — skipping.")
        return

    if _version_tuple(tag) > _version_tuple(VERSION):
        body  = (data.get("body") or "").strip()
        notes = "\n".join(body.splitlines()[:10])
        sha_match = re.search(r'\bsha256[:\s]+([0-9a-fA-F]{64})\b', body, re.IGNORECASE)
        # Detached Ed25519 signature over the raw zip bytes, base64-encoded,
        # provided as a `sig:<base64>` line in the release body.  This is the
        # trust root verified in download_and_apply() before extraction.
        sig_match = re.search(r'\bsig[:\s]+([A-Za-z0-9+/=]{80,200})\b', body)
        info  = {
            "version":     tag,
            "notes":       notes,
            "zipball_url": data.get("zipball_url", ""),
            "html_url":    data.get("html_url", ""),
            "sha256":      sha_match.group(1).lower() if sha_match else None,
            "sig":         sig_match.group(1) if sig_match else None,
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
    """Atomic write — both updater.py and whats_new_dialog.py share this file
    and a kill mid-write would leave it empty, dropping the user's snooze /
    last-seen-version state."""
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, STATE_PATH)
    except Exception as e:
        logger.debug("Failed to save update state: %s", e)
