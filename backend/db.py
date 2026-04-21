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
                expires_at REAL NOT NULL
            );

            -- Session tokens returned after successful PIN verification
            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT PRIMARY KEY,
                email      TEXT NOT NULL,
                expires_at REAL NOT NULL
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
        # Migration: add trial_started_at to existing databases
        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "trial_started_at" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN trial_started_at REAL DEFAULT NULL")
