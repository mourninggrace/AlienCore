"""
AlienCore - auth.py
Client-side authentication.  Handles PIN login, session token caching,
server refresh, and license info retrieval.

All server calls use stdlib urllib only — no third-party HTTP library needed.
"""

import hashlib
import hmac as _hmac
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request

from core.constants import USER_DATA_DIR

logger = logging.getLogger("aliencore.auth")

# Session file lives alongside the rest of writable user state in
# USER_DATA_DIR (%LOCALAPPDATA%\AlienCore\ in installer builds, project root
# in source builds).  Keeping it here means an uninstall + Local-AppData wipe
# actually severs the cached session — earlier builds put session.json under
# %APPDATA%\AlienCore\ (Roaming), which survived a Local wipe and silently
# auto-signed users back in after a "clean" reinstall.
_SESSION_DIR  = USER_DATA_DIR
_SESSION_PATH = os.path.join(_SESSION_DIR, "session.json")

# Legacy location (%APPDATA%\AlienCore\session.json) — read once at startup
# so users upgrading from <= 1.0.0 don't get forced to sign in again.  The
# legacy file is moved into the new location on first load_session() call.
_LEGACY_SESSION_PATH = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "AlienCore", "session.json",
)

# Grace period: if server is unreachable, allow this many seconds of offline use
_OFFLINE_GRACE_SECS = 72 * 3600   # 72 hours
_TRIAL_DAYS         = 30           # free trial length for new accounts

_lock    = threading.Lock()
_session: dict = {}   # in-memory cache


# ─────────────────────────────────────────────────────────────────────────────
# Startup
# ─────────────────────────────────────────────────────────────────────────────

def _session_sig(data: dict) -> str:
    from core import fingerprint as fp
    key     = hashlib.sha256(("aliencore-session|" + fp.get()).encode()).digest()
    payload = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return _hmac.new(key, payload, hashlib.sha256).hexdigest()


def _session_sig_valid(data: dict, sig: str) -> bool:
    try:
        return _hmac.compare_digest(_session_sig(data), sig)
    except Exception:
        return False


def _migrate_legacy_session_if_needed():
    """One-time move of session.json from the old Roaming AppData location
    into USER_DATA_DIR.  Done in-place (os.replace) so the file ends up
    exactly where the new code expects it; the HMAC remains valid because
    it's keyed on the hardware fingerprint, not the path.  Silently no-op
    if the new file already exists or the legacy one doesn't."""
    if _SESSION_PATH == _LEGACY_SESSION_PATH:
        return  # source build — both paths resolve to the same file
    if os.path.exists(_SESSION_PATH):
        return  # already migrated (or fresh install on the new path)
    if not os.path.exists(_LEGACY_SESSION_PATH):
        return
    try:
        os.makedirs(_SESSION_DIR, exist_ok=True)
        os.replace(_LEGACY_SESSION_PATH, _SESSION_PATH)
        logger.info("Migrated session.json from legacy %APPDATA% location.")
    except Exception as e:
        logger.debug("Legacy session migration failed: %s", e)


def load_session():
    """Load (or reload) the saved session from disk into memory.

    Reload-safe: if the session file is missing (e.g. a subprocess just
    called auth.logout() which removed it), the in-memory _session is
    cleared.  Without this, a Sign Out from the paywall subprocess
    leaves the parent process's _session global holding the stale
    pre-logout token — is_logged_in() returns True, the paywall loop's
    "fall back to login" branch misroutes to "exit", and the user has
    to manually relaunch AlienCore to see the login dialog again.
    """
    global _session
    _migrate_legacy_session_if_needed()
    if not os.path.exists(_SESSION_PATH):
        with _lock:
            _session = {}
        return
    try:
        with open(_SESSION_PATH, "r", encoding="utf-8") as f:
            outer = json.load(f)
        # v1 ships with HMAC-signed sessions only — there is no legacy
        # unsigned-session compatibility window.  An unsigned session.json
        # could be a hand-crafted file dropped by malware on the local
        # machine; reject it.
        if not (isinstance(outer, dict) and "d" in outer and "s" in outer):
            if isinstance(outer, dict) and outer.get("token"):
                logger.warning("Session file is unsigned — discarding (sign-in required)")
            return
        data, sig = outer["d"], outer["s"]
        if not isinstance(data, dict) or not _session_sig_valid(data, sig):
            logger.warning("Session file failed integrity check — discarding")
            return
        if data.get("token"):
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
        from core import fingerprint as fp
        resp = _post("/auth/check", {"token": s["token"], "fingerprint": fp.get()})
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


def needs_paywall() -> bool:
    """
    True when the user is signed in but their 30-day trial has ended and they
    haven't purchased a base license.  This is the hard-lock condition: every
    other launch path either has a paid license, an active trial, or no
    session (in which case the login dialog runs first).

    Distinct from `not is_on_trial()` because users who never started a trial
    (e.g. fresh sign-in mid-session-rejection) should fall through to the
    normal flow, not be paywalled.  The gate fires only when the server
    confirmed at some point that this account had a trial and the trial has
    since elapsed.
    """
    if not is_logged_in():
        return False
    if is_licensed():
        return False
    s = get_session()
    started = s.get("trial_started_at")
    if not started:
        return False
    return (time.time() - started) >= (_TRIAL_DAYS * 86400)


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
    if not _p.isdigit() or len(_p) != 6:
        return False, "PIN must be six digits."
    try:
        from core import fingerprint as fp
        if not fp.is_resolved():
            # Server will reject "unknown" anyway — fail fast with a clear message.
            return False, ("Hardware identification failed. AlienCore cannot "
                           "verify your machine — sign-in is disabled. Please "
                           "ensure you are running on a real Windows install "
                           "(not a sandboxed VM with restricted registry/WMI).")
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
        from core import fingerprint as fp
        resp = _post("/auth/check", {"token": s["token"], "fingerprint": fp.get()})
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
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "error": f"HTTP {e.code}"}


_TRIAL_SENTINEL = object()


def _safe_float(v, fallback: float = 0.0) -> float:
    """Convert v to float, rejecting NaN/inf which would break time-comparison
    logic (`time.time() < NaN` is always False, silently extending grace
    periods forever)."""
    import math
    try:
        f = float(v)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(f):
        return fallback
    return f


def _verify_license_payload(data: dict) -> bool:
    """Return True iff the server's response carries a valid Ed25519
    signature over the license payload.  Refuses to grant has_base / has_pro
    without one — this is the defense against an attacker who MITMs
    /auth/check and flips license bits to True."""
    from core import license_signing
    if not license_signing.is_configured():
        # Public key placeholder — pre-deploy / dev environments.  Trust
        # the server but log loudly so it's obvious this is not production.
        logger.warning(
            "License signature verification SKIPPED — public key not "
            "configured.  Pro/Base features unlock without proof of payment."
        )
        return True
    sig = data.get("license_sig")
    if not sig:
        logger.warning("Server response lacks license_sig — license rejected")
        return False
    if not license_signing.verify(data, sig):
        logger.warning("License signature INVALID — license rejected")
        return False
    return True


def _save_session(data: dict):
    global _session
    # Verify the server-side signature before trusting any license bits.
    # If the response is unsigned (server-side downgrade, MITM strip), or
    # the signature doesn't verify, treat the session as unlicensed — the
    # token still works for /auth/check but features stay locked until a
    # valid signed response arrives.
    if not _verify_license_payload(data):
        data = dict(data)
        data["has_base"] = False
        data["has_pro"]  = False
    # Preserve existing trial_started_at only if the server omitted the key
    # entirely (e.g. offline / dev login).  An explicit 0 / None from the
    # server means "no trial" and must override whatever was cached.
    with _lock:
        existing_trial = _session.get("trial_started_at")
    if "trial_started_at" in data:
        trial = data.get("trial_started_at")
    else:
        trial = existing_trial
    session = {
        "email":            data.get("email", ""),
        "token":            data.get("token", ""),
        "has_base":         bool(data.get("has_base")),
        "has_pro":          bool(data.get("has_pro")),
        "trial_started_at": trial,
        "expires_at":       _safe_float(data.get("expires_at", 0)),
        "last_verified_at": time.time(),
    }
    with _lock:
        _session = session
    _persist()


def _merge_session(data: dict):
    """Update license fields without touching the token."""
    if not _verify_license_payload(data):
        # Force-clear license bits when the response can't be verified.
        # This is the same defense as _save_session — a MITM that flips
        # has_pro=true is rejected even on rolling refresh.
        data = dict(data)
        data["has_base"] = False
        data["has_pro"]  = False
    with _lock:
        existing_trial = _session.get("trial_started_at")
        if "trial_started_at" in data:
            trial = data.get("trial_started_at")
        else:
            trial = existing_trial
        _session.update({
            "has_base":         bool(data.get("has_base")),
            "has_pro":          bool(data.get("has_pro")),
            "trial_started_at": trial,
            "expires_at":       _safe_float(data.get("expires_at"),
                                            _safe_float(_session.get("expires_at", 0))),
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
        sig = _session_sig(data)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"d": data, "s": sig}, f)
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

# Serial numbers are NOT stored here.  Instead we keep PBKDF2-SHA256 digests
# (600 000 iterations, app-specific salt).  A customer reading this source
# learns nothing useful — brute-forcing an 8-digit space at this work factor
# takes months on GPU.  Your dev access is unchanged: plug in the YubiKey and
# try_dev_unlock() hashes what it detects, then compares.
#
# To add a new YubiKey: run this in a Python shell and paste the output here —
#   import hashlib
#   SALT = b'AlienCore\x00devkey\x00v1'
#   print(hashlib.pbkdf2_hmac('sha256', b'<serial>', SALT, 600_000).hex())

_DEV_YUBIKEY_HASHES: set[str] = {
    "06dd3f28ec22749a84289293989a07b109813f599c56dca5c74b5d31b961e59b",  # copykitten
}

_PBKDF2_SALT       = b'AlienCore\x00devkey\x00v1'
_PBKDF2_ITERATIONS = 600_000


def _hash_serial(serial: str) -> str:
    """Return the PBKDF2-SHA256 digest of a YubiKey serial string."""
    import hashlib
    return hashlib.pbkdf2_hmac(
        "sha256", serial.encode(), _PBKDF2_SALT, _PBKDF2_ITERATIONS
    ).hex()


def _detect_yubikey_serials() -> set[str]:
    """Return serials of all YubiKeys currently plugged into this machine."""
    import os, subprocess
    # Absolute path — defeat PATH-hijack against the elevated AlienCore process.
    sysroot = os.environ.get("SystemRoot") or r"C:\Windows"
    pwsh = os.path.join(sysroot, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if not os.path.exists(pwsh):
        logger.debug("YubiKey detection: powershell.exe not found at %s", pwsh)
        return set()
    try:
        out = subprocess.check_output(
            [pwsh, "-NoProfile", "-NonInteractive", "-Command",
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
    if not ({_hash_serial(s) for s in _detect_yubikey_serials()} & _DEV_YUBIKEY_HASHES):
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
