"""
AlienCore Backend Server
========================
Deploy this on any Linux VPS (e.g. DigitalOcean $4/mo, Hetzner CX11).

Quick-start:
  pip install flask
  export AC_SMTP_USER=you@gmail.com
  export AC_SMTP_PASS=your_app_password
  export AC_SECRET=your_random_secret_32chars
  python -m backend.server

Production (behind nginx + gunicorn):
  pip install gunicorn
  gunicorn -w 2 -b 127.0.0.1:8765 "backend.server:app"

PayPal IPN setup:
  Log into PayPal → Account Settings → Notifications → Instant Payment Notifications
  Enable IPN, set Notification URL to:  https://YOUR_DOMAIN/paypal/ipn
"""

import datetime as _dt
import logging
import random
import re
import secrets
import time
import unicodedata
import urllib.parse
import urllib.request

from flask import Flask, request, jsonify

from backend import db, mail
from backend.config import (
    BACKEND_HOST, BACKEND_PORT,
    PIN_EXPIRY_MINUTES, TOKEN_EXPIRY_DAYS,
    SESSION_MAX_LIFETIME_DAYS,
    PIN_RESEND_COOLDOWN_SEC, PIN_PER_IP_DAILY_CAP, PIN_MAX_ATTEMPTS,
    LICENSE_PRIVATE_KEY_PATH, LICENSE_PRIVATE_KEY_PEM,
    PAYPAL_EMAIL, PAYPAL_MODE, PRODUCTS,
)


# ─────────────────────────────────────────────────────────────────────────────
# License signing (Ed25519)
# ─────────────────────────────────────────────────────────────────────────────

_LICENSE_PRIV_KEY = None


def _load_license_key():
    """Load the Ed25519 private key once at startup.  Crashes the process
    intentionally when the key isn't configured AND PayPal is in live mode —
    a production server with no signing key issues unsigned license payloads
    that the client refuses, locking out every paying customer."""
    global _LICENSE_PRIV_KEY
    pem = b""
    if LICENSE_PRIVATE_KEY_PATH:
        try:
            with open(LICENSE_PRIVATE_KEY_PATH, "rb") as f:
                pem = f.read()
        except Exception as e:
            logger.error("AC_LICENSE_PRIVATE_KEY_PATH unreadable: %s", e)
    elif LICENSE_PRIVATE_KEY_PEM:
        pem = LICENSE_PRIVATE_KEY_PEM.encode("utf-8")

    if not pem:
        if PAYPAL_MODE == "live":
            logger.critical(
                "License private key not configured — refusing to start in "
                "live mode. Run tools/generate_license_keypair.py and set "
                "AC_LICENSE_PRIVATE_KEY_PATH (or AC_LICENSE_PRIVATE_KEY)."
            )
            import sys
            sys.exit(2)
        logger.warning(
            "License private key not configured (sandbox mode) — "
            "responses will be unsigned and the client will reject them."
        )
        return

    try:
        from cryptography.hazmat.primitives import serialization
        _LICENSE_PRIV_KEY = serialization.load_pem_private_key(pem, password=None)
        logger.info("License signing key loaded.")
    except Exception as e:
        logger.error("Failed to load license private key: %s", e)
        if PAYPAL_MODE == "live":
            import sys
            sys.exit(2)


def _sign_license(payload: dict) -> dict:
    """Add `license_sig` and `signed_at` to a license payload.  Returns the
    same dict (mutated in place) so callers can `return jsonify(payload)`.

    Canonical signing string MUST match core/license_signing._canonical_bytes
    on the client byte-for-byte."""
    if _LICENSE_PRIV_KEY is None:
        return payload
    import base64
    payload["signed_at"] = int(time.time())
    parts = [
        payload.get("email", ""),
        "1" if payload.get("has_base") else "0",
        "1" if payload.get("has_pro") else "0",
        f"{int(payload.get('trial_started_at') or 0)}",
        f"{int(payload.get('expires_at') or 0)}",
        f"{int(payload.get('issued_at') or 0)}",
        f"{int(payload['signed_at'])}",
        payload.get("fingerprint", ""),
    ]
    canonical = "|".join(parts).encode("utf-8")
    sig = _LICENSE_PRIV_KEY.sign(canonical)
    payload["license_sig"] = base64.b64encode(sig).decode("ascii")
    return payload


def _normalize_email(raw: str) -> str:
    """Canonicalize an email address so a+1@gmail.com / A+1@GMAIL.COM /
    a@gmail.com all resolve to the same row.  Trial enforcement is per
    canonical email — the +tag form is the obvious bypass we have to close.

    Lowercase + NFKC normalize + strip everything from `+` to `@` in the
    local-part.  Domain is also lowercased.  Returns "" for invalid input
    so the caller can reject early.
    """
    if not isinstance(raw, str):
        return ""
    s = unicodedata.normalize("NFKC", raw).strip().lower()
    if "@" not in s or "." not in s.split("@")[-1]:
        return ""
    local, domain = s.rsplit("@", 1)
    if "+" in local:
        local = local.split("+", 1)[0]
    return f"{local}@{domain}"


def _client_ip() -> str:
    """Return the originating client IP, respecting X-Forwarded-For when the
    server is behind nginx/Cloudflare.  Returns "" if unknown."""
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or ""


def _today_utc() -> str:
    return _dt.datetime.utcnow().strftime("%Y-%m-%d")


# Generic "auth failed" message — never reveal which field was wrong, so
# the endpoint can't be used to enumerate which emails have outstanding PINs.
_AUTH_FAILED = {"ok": False, "error": "Invalid email or PIN. Request a new code if needed."}

app    = Flask(__name__)
logger = logging.getLogger("aliencore.backend")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-8s  %(message)s")

_PAYPAL_VERIFY = {
    "live":    "https://ipnpb.paypal.com/cgi-bin/webscr",
    "sandbox": "https://ipnpb.sandbox.paypal.com/cgi-bin/webscr",
}


# ─────────────────────────────────────────────────────────────────────────────
# Auth — send PIN
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/auth/send-pin", methods=["POST"])
def send_pin():
    data  = request.get_json(force=True, silent=True) or {}
    email = _normalize_email(data.get("email") or "")
    if not email:
        return jsonify({"ok": False, "error": "Invalid email address."}), 400

    ip  = _client_ip()
    now = time.time()
    today = _today_utc()

    with db.get_conn() as conn:
        # Per-IP daily cap.  Stops the endpoint becoming an open spam relay.
        cap_row = conn.execute(
            "SELECT count FROM pin_requests_by_ip WHERE ip=? AND day=?",
            (ip, today),
        ).fetchone()
        if cap_row and cap_row["count"] >= PIN_PER_IP_DAILY_CAP:
            logger.warning("PIN rate-limit hit for IP %s on %s", ip, today)
            return jsonify({"ok": False,
                            "error": "Too many requests from this network. "
                                     "Try again tomorrow."}), 429

        # Per-email cooldown.  60 s between successive PINs to the same address.
        prev = conn.execute(
            "SELECT last_sent_at FROM pins WHERE email=?", (email,)
        ).fetchone()
        if prev and prev["last_sent_at"]:
            elapsed = now - prev["last_sent_at"]
            if elapsed < PIN_RESEND_COOLDOWN_SEC:
                wait = int(PIN_RESEND_COOLDOWN_SEC - elapsed)
                return jsonify({"ok": False,
                                "error": f"Please wait {wait}s before requesting another code."}), 429

        pin        = f"{random.SystemRandom().randint(0, 999999):06d}"
        expires_at = now + PIN_EXPIRY_MINUTES * 60

        conn.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (email,))
        conn.execute(
            "INSERT OR REPLACE INTO pins "
            "(email, pin, expires_at, attempts, last_sent_at) "
            "VALUES (?,?,?,?,?)",
            (email, pin, expires_at, 0, now),
        )
        conn.execute(
            "INSERT INTO pin_requests_by_ip (ip, day, count) VALUES (?,?,1) "
            "ON CONFLICT(ip, day) DO UPDATE SET count = count + 1",
            (ip, today),
        )

    try:
        mail.send_pin_email(email, pin)
    except Exception as e:
        logger.error("Email send failed for %s: %s", email, e)
        # Don't echo internal exception details back to the client.
        return jsonify({"ok": False,
                        "error": "Email service is temporarily unavailable. "
                                 "Please try again in a few minutes."}), 500

    logger.info("PIN sent → %s  (ip=%s)", email, ip)
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────────────────────
# Auth — verify PIN → return token + license info
# ─────────────────────────────────────────────────────────────────────────────

_TRIAL_DAYS = 30


@app.route("/auth/verify-pin", methods=["POST"])
def verify_pin():
    data        = request.get_json(force=True, silent=True) or {}
    email       = _normalize_email(data.get("email") or "")
    pin         = (data.get("pin")         or "").strip()
    fingerprint = (data.get("fingerprint") or "").strip()[:64]  # cap length

    if not email or not pin:
        return jsonify({"ok": False, "error": "Email and PIN required."}), 400
    if not pin.isdigit() or len(pin) != 6:
        return jsonify(_AUTH_FAILED), 401
    # No legitimate AlienCore client ever submits an empty or "unknown"
    # fingerprint — that only happens when a sandboxed environment can't
    # read the hardware identifiers.  Reject so unbound trial farms can't
    # be created from VMs / locked-down sandboxes.
    if not fingerprint or fingerprint == "unknown":
        return jsonify({"ok": False,
                        "error": "Hardware identification failed. "
                                 "AlienCore needs to verify your machine "
                                 "before signing in."}), 400

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT pin, expires_at, attempts FROM pins WHERE email=?", (email,)
        ).fetchone()

        # Generic auth-failed responses below — don't reveal whether the
        # email has ever been issued a PIN.  Each branch logs the precise
        # cause server-side for debugging.
        if not row:
            logger.info("verify-pin: no PIN for %s", email)
            return jsonify(_AUTH_FAILED), 401
        if time.time() > row["expires_at"]:
            conn.execute("DELETE FROM pins WHERE email=?", (email,))
            logger.info("verify-pin: PIN expired for %s", email)
            return jsonify(_AUTH_FAILED), 401
        if row["attempts"] >= PIN_MAX_ATTEMPTS:
            conn.execute("DELETE FROM pins WHERE email=?", (email,))
            logger.warning("verify-pin: too many attempts for %s — PIN burnt", email)
            return jsonify(_AUTH_FAILED), 401
        # Constant-time PIN comparison so a network-based timing oracle
        # can't shave digits off the search space.
        if not secrets.compare_digest(row["pin"], pin):
            conn.execute(
                "UPDATE pins SET attempts = attempts + 1 WHERE email=?", (email,)
            )
            return jsonify(_AUTH_FAILED), 401

        # Consume the PIN (single use)
        conn.execute("DELETE FROM pins WHERE email=?", (email,))

        # Issue session token, bound to the submitting fingerprint at
        # creation time.  No more lazy-bind on first /auth/check.
        token      = secrets.token_urlsafe(40)
        now        = time.time()
        expires_at = now + TOKEN_EXPIRY_DAYS * 86400
        conn.execute(
            "INSERT OR REPLACE INTO sessions "
            "(token, email, expires_at, issued_at, fingerprint) "
            "VALUES (?,?,?,?,?)",
            (token, email, expires_at, now, fingerprint),
        )

        conn.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (email,))
        user = conn.execute(
            "SELECT has_base, has_pro, trial_started_at FROM users WHERE email=?",
            (email,),
        ).fetchone()

        # ── Trial logic ───────────────────────────────────────────────────────
        # Priority:
        #   1. User already has a paid base license → no trial needed.
        #   2. Email already has a trial timestamp → reuse it (returning user).
        #   3. Fingerprint matches a previous trial → carry that trial's start
        #      date over so reinstall / new email doesn't reset the clock.
        #      Exception: fingerprint has_paid=True (this machine already bought)
        #      so we don't penalise legitimate reinstalls.
        #   4. None of the above → first login ever on this machine + email.

        trial_started_at = user["trial_started_at"]

        if not user["has_base"] and trial_started_at is None:
            # Fingerprint is guaranteed real here — the early-return above
            # rejected empty/"unknown".  This closes the email-churn farm:
            # if this fingerprint already started a trial under a different
            # email, the new email inherits the same start date.
            hw_row = conn.execute(
                "SELECT trial_started_at, has_paid FROM hardware_fingerprints"
                " WHERE fingerprint=?",
                (fingerprint,),
            ).fetchone()

            if hw_row and not hw_row["has_paid"]:
                # This hardware already started a trial — use that start date.
                # If the original 30 days has elapsed, the new email is
                # immediately past-trial too.
                trial_started_at = hw_row["trial_started_at"]
                logger.info(
                    "Trial fingerprint match for %s — trial started %s ago",
                    email,
                    int(time.time() - trial_started_at),
                )
            else:
                # Fresh trial — record the start time
                trial_started_at = time.time()

            conn.execute(
                "UPDATE users SET trial_started_at=? WHERE email=?",
                (trial_started_at, email),
            )

            # Register the fingerprint row with the email that first claimed it.
            conn.execute(
                "INSERT OR IGNORE INTO hardware_fingerprints"
                " (fingerprint, first_email, trial_started_at)"
                " VALUES (?,?,?)",
                (fingerprint, email, trial_started_at),
            )

    logger.info("Login: %s  base=%s pro=%s  fingerprint=%s",
                email, bool(user["has_base"]), bool(user["has_pro"]),
                fingerprint[:8] + "…" if len(fingerprint) > 8 else fingerprint)
    payload = {
        "ok":               True,
        "token":             token,
        "email":             email,
        "has_base":          bool(user["has_base"]),
        "has_pro":           bool(user["has_pro"]),
        "trial_started_at":  trial_started_at,
        "expires_at":        expires_at,
        "issued_at":         now,
        "fingerprint":       fingerprint,
    }
    _sign_license(payload)
    return jsonify(payload)


# ─────────────────────────────────────────────────────────────────────────────
# Auth — validate existing token (called on every AlienCore startup)
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/auth/check", methods=["POST"])
def check_token():
    data        = request.get_json(force=True, silent=True) or {}
    token       = (data.get("token")       or "").strip()
    fingerprint = (data.get("fingerprint") or "").strip()[:64]
    if not token:
        return jsonify({"ok": False, "error": "Token required."}), 400

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT email, expires_at, issued_at, fingerprint "
            "FROM sessions WHERE token=?", (token,)
        ).fetchone()

        now = time.time()
        if not row or now > row["expires_at"]:
            return jsonify({"ok": False, "error": "Session expired."}), 401

        # Absolute lifetime cap — no rolling extension past this.  An
        # attacker who exfiltrates a token can no longer keep it alive
        # forever by calling /auth/check once a month.
        issued_at = row["issued_at"]
        if issued_at is not None:
            absolute_deadline = issued_at + SESSION_MAX_LIFETIME_DAYS * 86400
            if now > absolute_deadline:
                conn.execute("DELETE FROM sessions WHERE token=?", (token,))
                return jsonify({"ok": False,
                                "error": "Session expired. Please sign in again."}), 401

        stored_fp = row["fingerprint"]
        # Sessions issued under the new code are bound at creation; legacy
        # rows with NULL fingerprint were forcibly migrated, but defensively
        # treat any unbound row as expired rather than lazy-binding (which
        # let the first /auth/check claim ownership of an orphaned token).
        if not stored_fp:
            conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            logger.warning(
                "Session %s… had no fingerprint binding — rejected.", token[:8]
            )
            return jsonify({"ok": False,
                            "error": "Session expired. Please sign in again."}), 401

        if not fingerprint or fingerprint == "unknown":
            return jsonify({"ok": False,
                            "error": "Hardware identification failed."}), 400

        if stored_fp != fingerprint:
            logger.warning(
                "Fingerprint mismatch — token=%s…  stored=%s…  got=%s…",
                token[:8], stored_fp[:8], fingerprint[:8],
            )
            return jsonify({"ok": False,
                            "error": "Session is bound to another machine."}), 401

        email = row["email"]
        user  = conn.execute(
            "SELECT has_base, has_pro, trial_started_at FROM users WHERE email=?",
            (email,),
        ).fetchone()

        # Rolling expiry — refresh, but never past the absolute deadline.
        new_expiry = now + TOKEN_EXPIRY_DAYS * 86400
        if issued_at is not None:
            absolute_deadline = issued_at + SESSION_MAX_LIFETIME_DAYS * 86400
            if new_expiry > absolute_deadline:
                new_expiry = absolute_deadline
        conn.execute(
            "UPDATE sessions SET expires_at=? WHERE token=?", (new_expiry, token)
        )

    payload = {
        "ok":               True,
        "email":             email,
        "has_base":          bool(user["has_base"]),
        "has_pro":           bool(user["has_pro"]),
        "trial_started_at":  user["trial_started_at"],
        "expires_at":        new_expiry,
        "issued_at":         issued_at if issued_at is not None else 0,
        "fingerprint":       fingerprint,
    }
    _sign_license(payload)
    return jsonify(payload)


# ─────────────────────────────────────────────────────────────────────────────
# Auth — logout
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/auth/logout", methods=["POST"])
def logout():
    data  = request.get_json(force=True, silent=True) or {}
    token = (data.get("token") or "").strip()
    if token:
        with db.get_conn() as conn:
            conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────────────────────
# PayPal IPN — receives payment notifications from PayPal
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/paypal/ipn", methods=["POST"])
def paypal_ipn():
    raw_body = request.get_data()

    # Step 1: echo back to PayPal for verification
    verify_payload = b"cmd=_notify-validate&" + raw_body
    try:
        req = urllib.request.Request(
            _PAYPAL_VERIFY[PAYPAL_MODE],
            data=verify_payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            verify_status = resp.read().decode("utf-8")
    except Exception as e:
        logger.error("PayPal IPN verify request failed: %s", e)
        return "", 500

    if verify_status != "VERIFIED":
        logger.warning("PayPal IPN rejected (status=%s)", verify_status)
        return "", 400

    params = urllib.parse.parse_qs(raw_body.decode("utf-8"))
    def _p(k): return (params.get(k) or [""])[0]

    payment_status = _p("payment_status")
    receiver_email = _p("receiver_email").lower()
    txn_id         = _p("txn_id")
    custom         = _p("custom").strip().lower()   # user's AlienCore email
    item_number    = _p("item_number")
    mc_gross       = _p("mc_gross")
    mc_currency    = _p("mc_currency")

    logger.info("IPN: %s  txn=%s  item=%s  amount=%s %s  user=%s",
                payment_status, txn_id, item_number,
                mc_gross, mc_currency, custom)

    # Validate receiver is our PayPal account
    if receiver_email != PAYPAL_EMAIL.lower():
        logger.warning("IPN receiver mismatch: %s", receiver_email)
        return "", 400

    # Only process completed payments
    if payment_status == "Refunded":
        # Refunds: revoke the relevant license
        _handle_refund(txn_id)
        return "", 200
    if payment_status != "Completed":
        return "", 200

    if mc_currency != "USD":
        logger.warning("IPN unexpected currency: %s", mc_currency)
        return "", 200

    if not custom or "@" not in custom:
        logger.warning("IPN missing user email in custom field")
        return "", 400

    # Validate item and amount
    product = PRODUCTS.get(item_number)
    if not product:
        logger.warning("IPN unknown item_number=%s txn=%s — rejected", item_number, txn_id)
        return "", 400
    try:
        gross_float = float(mc_gross) if mc_gross else 0.0
    except ValueError:
        logger.warning("IPN unparseable mc_gross: %r", mc_gross)
        return "", 400
    if gross_float < float(product["amount"]) - 0.01:
        logger.warning("IPN amount mismatch: got %s, expected %s",
                       mc_gross, product["amount"])
        return "", 400

    email = custom
    with db.get_conn() as conn:
        if conn.execute(
            "SELECT txn_id FROM purchases WHERE txn_id=?", (txn_id,)
        ).fetchone():
            return "", 200   # duplicate — already processed

        conn.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (email,))

        # Pro requires base. If a Pro payment arrives for a user who doesn't
        # own base (client gate bypassed, or someone hand-crafted a PayPal
        # request), record the purchase as needing manual refund and skip
        # the grant.
        if item_number == "AC_PRO":
            row = conn.execute(
                "SELECT has_base FROM users WHERE email=?", (email,)
            ).fetchone()
            if not row or not row["has_base"]:
                conn.execute(
                    "INSERT INTO purchases (txn_id, email, product, amount, status)"
                    " VALUES (?,?,?,?,?)",
                    (txn_id, email, item_number, mc_gross, "rejected_no_base"),
                )
                logger.warning(
                    "REJECTED PRO grant: %s paid for AC_PRO without owning "
                    "AC_BASE. Txn %s recorded for manual refund.",
                    email, txn_id,
                )
                return "", 200

        conn.execute(
            "INSERT INTO purchases (txn_id, email, product, amount, status)"
            " VALUES (?,?,?,?,?)",
            (txn_id, email, item_number, mc_gross, "completed"),
        )

        if item_number == "AC_BASE":
            conn.execute("UPDATE users SET has_base=1 WHERE email=?", (email,))
            # Mark all fingerprints tied to this email as paid so they don't
            # get penalised by the trial-carry-over logic on reinstall.
            conn.execute(
                "UPDATE hardware_fingerprints SET has_paid=1 WHERE first_email=?",
                (email,),
            )
            logger.info("Granted BASE license to %s", email)
        elif item_number == "AC_PRO":
            conn.execute("UPDATE users SET has_pro=1 WHERE email=?", (email,))
            logger.info("Granted PRO add-on to %s", email)

    return "", 200


def _handle_refund(txn_id: str):
    """Reverse a license grant when PayPal issues a refund."""
    with db.get_conn() as conn:
        purchase = conn.execute(
            "SELECT email, product FROM purchases WHERE txn_id=?", (txn_id,)
        ).fetchone()
        if not purchase:
            return
        email, item = purchase["email"], purchase["product"]
        conn.execute(
            "UPDATE purchases SET status='refunded' WHERE txn_id=?", (txn_id,)
        )
        if item == "AC_BASE":
            conn.execute("UPDATE users SET has_base=0 WHERE email=?", (email,))
        elif item == "AC_PRO":
            conn.execute("UPDATE users SET has_pro=0  WHERE email=?", (email,))
        logger.info("Refund processed: %s → %s", txn_id, email)


# ─────────────────────────────────────────────────────────────────────────────
# Support — relay feedback / bug reports to the support inbox
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/support/submit", methods=["POST"])
def support_submit():
    data = request.get_json(force=True, silent=True) or {}
    email        = (data.get("email") or "").strip().lower()
    raw_type     = (data.get("type") or "Feedback").strip()[:40]
    # Strip CR/LF so the value can never inject into the email subject
    # (line splitting → header injection on naive mail clients).
    feedback_type = re.sub(r"[\r\n\x00]", " ", raw_type)
    description  = (data.get("description") or "").strip()
    sysinfo      = (data.get("sysinfo") or "").strip()

    if not email or "@" not in email:
        return jsonify({"ok": False, "error": "Valid email required."}), 400
    if not description:
        return jsonify({"ok": False, "error": "Description is empty."}), 400
    # Length caps so a runaway log can't blow up the email
    if len(description) > 20000:
        description = description[:20000] + "\n...(truncated)"
    if len(sysinfo) > 60000:
        sysinfo = sysinfo[:60000] + "\n...(truncated)"

    try:
        mail.send_feedback_email(email, feedback_type, description, sysinfo)
    except Exception as e:
        logger.error("Feedback relay failed for %s: %s", email, e)
        return jsonify({"ok": False,
                        "error": "Feedback service is temporarily unavailable. "
                                 "Please try again later."}), 500

    logger.info("Feedback relayed <- %s (%s)", email, feedback_type)
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────────────────────
# Health check
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    # No version disclosure — would aid targeted attacks if a CVE is later
    # disclosed for a specific point release.
    return jsonify({"ok": True, "service": "AlienCore API"})


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    db.init_db()
    _load_license_key()
    logger.info("AlienCore backend starting on %s:%d", BACKEND_HOST, BACKEND_PORT)
    app.run(host=BACKEND_HOST, port=BACKEND_PORT, debug=False)
else:
    # gunicorn or any other WSGI host imports this module without running
    # __main__ — make sure the key still loads.
    _load_license_key()
