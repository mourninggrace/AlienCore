"""
AlienCore Backend - config.py
All settings are read from environment variables so the server can be
configured without touching source code.  Edit the defaults below only
for local development.

Deployment checklist:
  AC_SECRET     — long random string (openssl rand -hex 32)
  AC_SMTP_USER  — Gmail address you'll send PINs from
  AC_SMTP_PASS  — Gmail App Password (not your regular password)
  AC_PAYPAL_EMAIL — your PayPal account email (mourning.grace.2014@gmail.com)
  AC_PAYPAL_MODE  — "live" for real payments, "sandbox" for testing

Run the server:
  pip install flask
  python -m backend.server
"""

import os

# ── Server ────────────────────────────────────────────────────────────────────
BACKEND_HOST = os.getenv("AC_HOST",   "0.0.0.0")
BACKEND_PORT = int(os.getenv("AC_PORT", "8765"))

# Long random secret — used to protect the /paypal/refund-support admin endpoint
SECRET_KEY   = os.getenv("AC_SECRET", "CHANGE_ME_IN_PRODUCTION")

# ── SMTP (email sending) ──────────────────────────────────────────────────────
# Recommended: Gmail + App Password (2FA must be enabled on the Gmail account)
# Google account → Security → App Passwords → generate one for "Mail"
SMTP_HOST  = os.getenv("AC_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT  = int(os.getenv("AC_SMTP_PORT", "587"))
SMTP_USER  = os.getenv("AC_SMTP_USER", "")   # e.g. yourname@gmail.com
SMTP_PASS  = os.getenv("AC_SMTP_PASS", "")   # Gmail App Password
FROM_EMAIL = os.getenv("AC_FROM_EMAIL", SMTP_USER)
FROM_NAME  = "AlienCore"

# ── PayPal ────────────────────────────────────────────────────────────────────
# Set to your PayPal business account email.
# In PayPal → Profile → Account Settings → Notifications → IPN:
#   Enable IPN, set Notification URL to https://YOUR_SERVER/paypal/ipn
PAYPAL_EMAIL = os.getenv("AC_PAYPAL_EMAIL", "mourning.grace.2014@gmail.com")
PAYPAL_MODE  = os.getenv("AC_PAYPAL_MODE",  "live")   # "live" | "sandbox"

# Kyle's notification email — support tickets land here
KYLE_EMAIL   = os.getenv("AC_KYLE_EMAIL", "mourning.grace.2014@gmail.com")

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH = os.getenv("AC_DB_PATH", "aliencore.db")

# ── Expiry ────────────────────────────────────────────────────────────────────
PIN_EXPIRY_MINUTES = 10
TOKEN_EXPIRY_DAYS  = 30

# ── Products (item_number must match what you set in PayPal button) ───────────
PRODUCTS = {
    "AC_BASE":    {"name": "AlienCore — Lifetime License",      "amount": "20.00"},
    "AC_PRO":     {"name": "AlienCore Pro Add-on",              "amount": "5.00"},
    "AC_SUPPORT": {"name": "AlienCore Priority Support (1x)",   "amount": "5.00"},
}
