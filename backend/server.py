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

import logging
import random
import secrets
import time
import urllib.parse
import urllib.request

from flask import Flask, request, jsonify

from backend import db, mail
from backend.config import (
    BACKEND_HOST, BACKEND_PORT, SECRET_KEY,
    PIN_EXPIRY_MINUTES, TOKEN_EXPIRY_DAYS,
    PAYPAL_EMAIL, PAYPAL_MODE, PRODUCTS,
)

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
    email = (data.get("email") or "").strip().lower()
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        return jsonify({"ok": False, "error": "Invalid email address."}), 400

    pin        = f"{random.SystemRandom().randint(0, 999999):06d}"
    expires_at = time.time() + PIN_EXPIRY_MINUTES * 60

    with db.get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (email,))
        conn.execute(
            "INSERT OR REPLACE INTO pins (email, pin, expires_at) VALUES (?,?,?)",
            (email, pin, expires_at),
        )

    try:
        mail.send_pin_email(email, pin)
    except Exception as e:
        logger.error("Email send failed for %s: %s", email, e)
        return jsonify({"ok": False,
                        "error": f"Could not send email: {e}"}), 500

    logger.info("PIN sent → %s", email)
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────────────────────
# Auth — verify PIN → return token + license info
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/auth/verify-pin", methods=["POST"])
def verify_pin():
    data  = request.get_json(force=True, silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    pin   = (data.get("pin")   or "").strip()

    if not email or not pin:
        return jsonify({"ok": False, "error": "Email and PIN required."}), 400

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT pin, expires_at FROM pins WHERE email=?", (email,)
        ).fetchone()

        if not row:
            return jsonify({"ok": False,
                            "error": "No PIN found. Request a new one."}), 401
        if time.time() > row["expires_at"]:
            return jsonify({"ok": False,
                            "error": "PIN expired. Request a new one."}), 401
        if row["pin"] != pin:
            return jsonify({"ok": False, "error": "Incorrect PIN."}), 401

        # Consume the PIN (single use)
        conn.execute("DELETE FROM pins WHERE email=?", (email,))

        # Issue session token
        token      = secrets.token_urlsafe(40)
        expires_at = time.time() + TOKEN_EXPIRY_DAYS * 86400
        conn.execute(
            "INSERT OR REPLACE INTO sessions (token, email, expires_at) VALUES (?,?,?)",
            (token, email, expires_at),
        )

        user = conn.execute(
            "SELECT has_base, has_pro, support_credits FROM users WHERE email=?",
            (email,),
        ).fetchone()

    logger.info("Login: %s  base=%s pro=%s", email,
                bool(user["has_base"]), bool(user["has_pro"]))
    return jsonify({
        "ok":              True,
        "token":           token,
        "email":           email,
        "has_base":        bool(user["has_base"]),
        "has_pro":         bool(user["has_pro"]),
        "support_credits": user["support_credits"],
        "expires_at":      expires_at,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Auth — validate existing token (called on every AlienCore startup)
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/auth/check", methods=["POST"])
def check_token():
    data  = request.get_json(force=True, silent=True) or {}
    token = (data.get("token") or "").strip()
    if not token:
        return jsonify({"ok": False, "error": "Token required."}), 400

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT email, expires_at FROM sessions WHERE token=?", (token,)
        ).fetchone()

        if not row or time.time() > row["expires_at"]:
            return jsonify({"ok": False, "error": "Session expired."}), 401

        email = row["email"]
        user  = conn.execute(
            "SELECT has_base, has_pro, support_credits FROM users WHERE email=?",
            (email,),
        ).fetchone()

        # Rolling expiry — refresh on each successful check
        new_expiry = time.time() + TOKEN_EXPIRY_DAYS * 86400
        conn.execute(
            "UPDATE sessions SET expires_at=? WHERE token=?", (new_expiry, token)
        )

    return jsonify({
        "ok":              True,
        "email":           email,
        "has_base":        bool(user["has_base"]),
        "has_pro":         bool(user["has_pro"]),
        "support_credits": user["support_credits"],
        "expires_at":      new_expiry,
    })


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

    # Validate the amount matches the product price
    product = PRODUCTS.get(item_number)
    try:
        gross_float = float(mc_gross) if mc_gross else 0.0
    except ValueError:
        logger.warning("IPN unparseable mc_gross: %r", mc_gross)
        return "", 400
    if product and gross_float < float(product["amount"]) - 0.01:
        logger.warning("IPN amount mismatch: got %s, expected %s",
                       mc_gross, product["amount"])
        return "", 400

    email = custom
    with db.get_conn() as conn:
        if conn.execute(
            "SELECT txn_id FROM purchases WHERE txn_id=?", (txn_id,)
        ).fetchone():
            return "", 200   # duplicate — already processed

        conn.execute(
            "INSERT INTO purchases (txn_id, email, product, amount, status)"
            " VALUES (?,?,?,?,?)",
            (txn_id, email, item_number, mc_gross, "completed"),
        )
        conn.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (email,))

        if item_number == "AC_BASE":
            conn.execute("UPDATE users SET has_base=1 WHERE email=?", (email,))
            logger.info("Granted BASE license to %s", email)
        elif item_number == "AC_PRO":
            conn.execute("UPDATE users SET has_pro=1 WHERE email=?", (email,))
            logger.info("Granted PRO add-on to %s", email)
        elif item_number == "AC_SUPPORT":
            conn.execute(
                "UPDATE users SET support_credits=support_credits+1 WHERE email=?",
                (email,),
            )
            logger.info("Granted support credit to %s", email)

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
        elif item == "AC_SUPPORT":
            conn.execute(
                "UPDATE users SET support_credits=MAX(0, support_credits-1)"
                " WHERE email=?", (email,)
            )
        logger.info("Refund processed: %s → %s", txn_id, email)


# ─────────────────────────────────────────────────────────────────────────────
# Support ticket
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/support/submit", methods=["POST"])
def submit_support():
    data    = request.get_json(force=True, silent=True) or {}
    token   = (data.get("token")   or "").strip()
    message = (data.get("message") or "").strip()

    if not message:
        return jsonify({"ok": False, "error": "Message cannot be empty."}), 400

    with db.get_conn() as conn:
        sess = conn.execute(
            "SELECT email, expires_at FROM sessions WHERE token=?", (token,)
        ).fetchone()
        if not sess or time.time() > sess["expires_at"]:
            return jsonify({"ok": False, "error": "Not logged in."}), 401

        email = sess["email"]
        user  = conn.execute(
            "SELECT support_credits FROM users WHERE email=?", (email,)
        ).fetchone()

        if not user or user["support_credits"] < 1:
            return jsonify({
                "ok":    False,
                "error": "No support credits. Purchase Priority Support first.",
            }), 403

        conn.execute(
            "UPDATE users SET support_credits=support_credits-1 WHERE email=?",
            (email,),
        )
        conn.execute(
            "INSERT INTO support_tickets (email, message) VALUES (?,?)",
            (email, message),
        )

    try:
        mail.send_support_notification(email, message)
    except Exception as e:
        logger.error("Failed to notify Kyle of support ticket: %s", e)

    logger.info("Support ticket from %s", email)
    return jsonify({
        "ok":      True,
        "message": "Received. Kyle will respond within 24 hours.",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Admin — remove a support credit before issuing a PayPal refund
# POST /paypal/refund-support  {"admin_key": SECRET, "email": "user@example.com"}
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/paypal/refund-support", methods=["POST"])
def refund_support():
    data  = request.get_json(force=True, silent=True) or {}
    if data.get("admin_key") != SECRET_KEY:
        return jsonify({"ok": False, "error": "Unauthorized."}), 403
    email = (data.get("email") or "").strip().lower()
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE users SET support_credits=MAX(0, support_credits-1)"
            " WHERE email=?", (email,)
        )
    logger.info("Admin: removed support credit from %s", email)
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────────────────────
# Health check
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "service": "AlienCore API", "version": "1.0.0"})


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    db.init_db()
    logger.info("AlienCore backend starting on %s:%d", BACKEND_HOST, BACKEND_PORT)
    app.run(host=BACKEND_HOST, port=BACKEND_PORT, debug=False)
