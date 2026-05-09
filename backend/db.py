"""
AlienCore Backend - db.py
SQLite database helpers.  Schema is created automatically on first run.
"""

import sqlite3
from backend.config import DB_PATH


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # safe for concurrent reads
    return conn


def init_db():
    """Create all tables if they don't exist. Safe to call on every startup."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                email            TEXT PRIMARY KEY,
                has_base         INTEGER NOT NULL DEFAULT 0,
                has_pro          INTEGER NOT NULL DEFAULT 0,
                trial_started_at REAL    DEFAULT NULL,
                created_at       REAL    NOT NULL DEFAULT (unixepoch())
            );

            -- One-time login PINs (overwritten on each request)
            CREATE TABLE IF NOT EXISTS pins (
                email      TEXT PRIMARY KEY,
                pin        TEXT NOT NULL,
                expires_at REAL NOT NULL,
                attempts   INTEGER NOT NULL DEFAULT 0,
                last_sent_at REAL DEFAULT NULL
            );

            -- Session tokens returned after successful PIN verification
            CREATE TABLE IF NOT EXISTS sessions (
                token       TEXT PRIMARY KEY,
                email       TEXT NOT NULL,
                expires_at  REAL NOT NULL,
                issued_at   REAL DEFAULT NULL,
                fingerprint TEXT DEFAULT NULL
            );

            -- Per-IP request log for abuse rate limiting on /auth/send-pin
            CREATE TABLE IF NOT EXISTS pin_requests_by_ip (
                ip       TEXT NOT NULL,
                day      TEXT NOT NULL,   -- YYYY-MM-DD UTC
                count    INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (ip, day)
            );

            -- Per-IP failed-verify counter for /auth/verify-pin.  Per-PIN
            -- attempts cap (PIN_MAX_ATTEMPTS=5) only stops single-IP brute
            -- force; a botnet sharing many IPs can combine to crack the
            -- 6-digit space.  This bucket is incremented on every verify
            -- failure regardless of whether the PIN row existed, so an
            -- attacker probing arbitrary emails still consumes their quota.
            CREATE TABLE IF NOT EXISTS pin_verify_attempts_by_ip (
                ip       TEXT NOT NULL,
                day      TEXT NOT NULL,   -- YYYY-MM-DD UTC
                count    INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (ip, day)
            );

            -- Audit trail of all PayPal payments received
            CREATE TABLE IF NOT EXISTS purchases (
                txn_id     TEXT PRIMARY KEY,
                email      TEXT,
                product    TEXT,
                amount     TEXT,
                status     TEXT,
                created_at REAL NOT NULL DEFAULT (unixepoch())
            );

            -- Hardware fingerprints — one row per physical machine.
            -- Used to prevent trial reset by reinstalling or using a new email.
            -- A fingerprint maps to the first trial_started_at for that machine.
            -- has_paid is set True when any purchase is linked to this fingerprint,
            -- which prevents false positives when a paying user reinstalls.
            CREATE TABLE IF NOT EXISTS hardware_fingerprints (
                fingerprint      TEXT PRIMARY KEY,
                first_email      TEXT NOT NULL,
                trial_started_at REAL NOT NULL DEFAULT (unixepoch()),
                has_paid         INTEGER NOT NULL DEFAULT 0
            );
        """)
        # Migrations
        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "trial_started_at" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN trial_started_at REAL DEFAULT NULL")

        sess_cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
        if "fingerprint" not in sess_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN fingerprint TEXT DEFAULT NULL")
        if "issued_at" not in sess_cols:
            # Backfill existing rows with their current expires_at minus
            # TOKEN_EXPIRY_DAYS so the absolute-lifetime cap kicks in
            # immediately on the next refresh; that's deliberate — pre-cap
            # tokens shouldn't get a free extra 90 days.
            conn.execute("ALTER TABLE sessions ADD COLUMN issued_at REAL DEFAULT NULL")
            conn.execute(
                "UPDATE sessions SET issued_at = expires_at - (30*86400) "
                "WHERE issued_at IS NULL"
            )

        pin_cols = {row[1] for row in conn.execute("PRAGMA table_info(pins)")}
        if "attempts" not in pin_cols:
            conn.execute("ALTER TABLE pins ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
        if "last_sent_at" not in pin_cols:
            conn.execute("ALTER TABLE pins ADD COLUMN last_sent_at REAL DEFAULT NULL")
