"""
AlienCore - auth.py
Client-side authentication.  Handles PIN login, session token caching,
server refresh, and license info retrieval.

All server calls use stdlib urllib only — no third-party HTTP library needed.
"""

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request

logger = logging.getLogger("aliencore.auth")

# Session file location: %APPDATA%\AlienCore\session.json
_SESSION_DIR  = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "AlienCore"
)
_SESSION_PATH = os.path.join(_SESSION_DIR, "session.json")

# Grace period: if server is unreachable, allow this many seconds of offline use
_OFFLINE_GRACE_SECS = 72 * 3600   # 72 hours
_TRIAL_DAYS         = 30           # free trial length for new accounts

_lock    = threading.Lock()
_session: dict = {}   # in-memory cache


# ─────────────────────────────────────────────────────────────────────────────
# Startup
# ─────────────────────────────────────────────────────────────────────────────

def load_session():
    """Load any saved session from disk into memory. Call once at startup."""
    global _session
    try:
        if os.path.exists(_SESSION_PATH):
            with open(_SESSION_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("token"):
                with _lock:
                    _session = data
                logger.info("Session loaded for %s", data.get("email", "?"))
    except Exception as e:
        logger.debug("Session load error: %s", e)


def refresh_session_async():
    """
    Validate the cached token against the server in the background.
    Updates license fields if they changed (e.g. user just purchased).
    Does NOT block startup — call this after load_session().
    """
    threading.Thread(target=_refresh_blocking, daemon=True,
                     name="AuthRefresh").start()


def _refresh_blocking():
    s = get_session()
    if not s.get("token"):
        return
    if s.get("expires_at", 0) > time.time() + 180 * 86400:
        return
    try:
        resp = _post("/auth/check", {"token": s["token"]})
        if resp.get("ok"):
            _merge_session(resp)
            logger.info("Session refreshed for %s  base=%s pro=%s",
                        resp.get("email"), resp.get("has_base"), resp.get("has_pro"))
        else:
            # Server explicitly rejected the token — clear it
            logger.info("Session rejected by server: %s", resp.get("error"))
            _clear_session()
    except Exception as e:
        logger.debug("Session refresh error (offline?): %s", e)
        # Leave the session as-is; offline grace applies via is_within_grace()


# ─────────────────────────────────────────────────────────────────────────────
# Session state queries
# ─────────────────────────────────────────────────────────────────────────────

def get_session() -> dict:
    with _lock:
        return dict(_session)


def is_logged_in() -> bool:
    """True if a non-expired session exists (locally — doesn't call server)."""
    s = get_session()
    if not s.get("token"):
        return False
    # Local expiry check
    if time.time() < s.get("expires_at", 0):
        return True
    # Expired token but within grace period and last_verified_at recent enough
    return _is_within_grace(s)


def is_licensed() -> bool:
    """True if the user has paid for the base license ($19.99)."""
    return is_logged_in() and bool(get_session().get("has_base"))


def is_on_trial() -> bool:
    """
    True if the user is within their 30-day free trial window.
    Trial grants access to base features only — Pro features remain locked.
    Trial begins on first login and is stored server-side; the timestamp is
    cached locally in the session file so it survives offline restarts.
    """
    if not is_logged_in():
        return False
    if is_licensed():
        return False   # paid users are never "on trial"
    s = get_session()
    started = s.get("trial_started_at")
    if started is None:
        return False
    return (time.time() - started) < (_TRIAL_DAYS * 86400)


def trial_days_left() -> int:
    """
    Days remaining in the free trial.  Returns 0 if trial expired or not active.
    """
    s = get_session()
    started = s.get("trial_started_at")
    if started is None:
        return 0
    remaining = _TRIAL_DAYS - int((time.time() - started) / 86400)
    return max(0, remaining)


def is_pro() -> bool:
    """True if the user has the Pro add-on (+$4.99)."""
    return is_licensed() and bool(get_session().get("has_pro"))


def get_email() -> str:
    return get_session().get("email", "")


def _is_within_grace(s: dict) -> bool:
    last = s.get("last_verified_at", 0)
    return (time.time() - last) < _OFFLINE_GRACE_SECS


# ─────────────────────────────────────────────────────────────────────────────
# Auth actions
# ─────────────────────────────────────────────────────────────────────────────

def send_pin(email: str) -> tuple[bool, str]:
    """Request a login PIN be emailed to the given address."""
    try:
        resp = _post("/auth/send-pin", {"email": email.strip().lower()})
        return resp.get("ok", False), resp.get("error", "PIN sent successfully.")
    except urllib.error.URLError:
        return False, "Cannot reach AlienCore server. Check your internet connection."
    except Exception as e:
        return False, f"Error: {e}"


def verify_pin(email: str, pin: str) -> tuple[bool, str]:
    """Submit the PIN. On success, session is saved to disk."""
    _p = pin.strip()
    try:
        from core import fingerprint as fp
        resp = _post("/auth/verify-pin", {
            "email":       email.strip().lower(),
            "pin":         _p,
            "fingerprint": fp.get(),
        })
        if resp.get("ok"):
            _save_session(resp)
            return True, "Signed in successfully."
        return False, resp.get("error", "Verification failed.")
    except urllib.error.URLError:
        return False, "Cannot reach AlienCore server. Check your internet connection."
    except Exception as e:
        return False, f"Error: {e}"


def refresh_license() -> tuple[bool, str]:
    """
    Manually refresh license info from the server (e.g. after purchasing).
    Returns (changed: bool, message: str).
    """
    s = get_session()
    if not s.get("token"):
        return False, "Not logged in."
    try:
        resp = _post("/auth/check", {"token": s["token"]})
        if resp.get("ok"):
            old_base = s.get("has_base")
            old_pro  = s.get("has_pro")
            _merge_session(resp)
            new = get_session()
            parts = []
            if new.get("has_base") and not old_base:
                parts.append("Base license activated!")
            if new.get("has_pro") and not old_pro:
                parts.append("Pro add-on activated!")
            return True, "  ".join(parts) if parts else "License is up to date."
        _clear_session()
        return False, resp.get("error", "Session expired. Please sign in again.")
    except urllib.error.URLError:
        return False, "Cannot reach server."
    except Exception as e:
        return False, f"Error: {e}"


def logout():
    """Sign out and clear all local session data."""
    s = get_session()
    if s.get("token"):
        try:
            _post("/auth/logout", {"token": s["token"]})
        except Exception:
            pass
    _clear_session()
    logger.info("Logged out.")


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _post(endpoint: str, payload: dict) -> dict:
    from core.constants import BACKEND_URL
    url  = BACKEND_URL.rstrip("/") + endpoint
    body = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _save_session(data: dict):
    global _session
    # Preserve existing trial_started_at if server didn't send one
    # (e.g. offline dev / backdoor login)
    with _lock:
        existing_trial = _session.get("trial_started_at")
    trial = data.get("trial_started_at") or existing_trial
    session = {
        "email":            data.get("email", ""),
        "token":            data.get("token", ""),
        "has_base":         bool(data.get("has_base")),
        "has_pro":          bool(data.get("has_pro")),
        "trial_started_at": trial,
        "expires_at":       float(data.get("expires_at", 0)),
        "last_verified_at": time.time(),
    }
    with _lock:
        _session = session
    _persist()


def _merge_session(data: dict):
    """Update license fields without touching the token."""
    with _lock:
        existing_trial = _session.get("trial_started_at")
        _session.update({
            "has_base":         bool(data.get("has_base")),
            "has_pro":          bool(data.get("has_pro")),
            "trial_started_at": data.get("trial_started_at") or existing_trial,
            "expires_at":       float(data.get("expires_at", _session.get("expires_at", 0))),
            "last_verified_at": time.time(),
        })
    _persist()


def _clear_session():
    global _session
    with _lock:
        _session = {}
    try:
        if os.path.exists(_SESSION_PATH):
            os.remove(_SESSION_PATH)
    except Exception:
        pass


def _persist():
    try:
        os.makedirs(_SESSION_DIR, exist_ok=True)
        tmp = _SESSION_PATH + ".tmp"
        with _lock:
            data = dict(_session)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, _SESSION_PATH)
    except Exception as e:
        logger.debug("Session persist error: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Developer YubiKey bypass
#
# Grants a full in-memory session when a dev-allowlisted YubiKey is plugged in.
# Works on any machine — the serial number is burned into the YubiKey's chip
# by Yubico and cannot be modified, so an attacker reading this source cannot
# spoof it without physically holding the matching YubiKey. The session is
# never persisted to disk, so pulling the YubiKey and restarting returns to
# normal login behaviour.
# ─────────────────────────────────────────────────────────────────────────────

_DEV_YUBIKEY_SERIALS: set[str] = {
    "26483466",   # copykitten
}


def _detect_yubikey_serials() -> set[str]:
    """Return serials of all YubiKeys currently plugged into this machine."""
    import subprocess
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "Get-PnpDevice -PresentOnly | "
             "Where-Object { $_.InstanceId -match "
             "'^USB\\\\VID_1050&PID_[0-9A-Fa-f]+\\\\\\d+$' } | "
             "ForEach-Object { ($_.InstanceId -split '\\\\')[-1] }"],
            text=True, timeout=20,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        serials = set()
        for line in out.splitlines():
            line = line.strip().lstrip("0")
            if line and line.isdigit():
                serials.add(line)
        return serials
    except Exception as e:
        logger.debug("YubiKey detection failed: %s", e)
        return set()


def try_dev_unlock() -> bool:
    """If a developer YubiKey is plugged in, activate an in-memory dev session
    (not persisted to disk). Returns True if the session was activated."""
    global _session
    if not (_detect_yubikey_serials() & _DEV_YUBIKEY_SERIALS):
        return False
    with _lock:
        _session = {
            "email":            "dev@aliencore.local",
            "token":            "dev-yubikey",
            "has_base":         True,
            "has_pro":          True,
            "trial_started_at": None,
            "expires_at":       time.time() + 365 * 86400,
            "last_verified_at": time.time(),
        }
    logger.info("Developer YubiKey detected — in-memory dev session active.")
    return True
