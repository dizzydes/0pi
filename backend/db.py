from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "db.sqlite"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    # Apply useful SQLite PRAGMAs
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    cols = [row[1] for row in cur.fetchall()]
    cur.close()
    return cols


def _ensure_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()

    # Detect legacy schema (providers.id, endpoints, etc.) and rebuild if found
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='providers'")
    row = cur.fetchone()
    legacy = False
    if row:
        cols = _columns(conn, 'providers')
        if 'provider_id' not in cols:
            legacy = True
    if legacy:
        # Dev-only rebuild: drop legacy tables
        for t in ("api_calls", "endpoints", "services", "providers"):
            cur.execute(f"DROP TABLE IF EXISTS {t}")

    # Providers
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS providers (
            provider_id TEXT PRIMARY KEY,
            provider_name TEXT NOT NULL,
            cdp_wallet_id TEXT NOT NULL,
            payout_wallet TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    # Services
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS services (
            service_id TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL,
            api_docs_url TEXT NOT NULL,
            price_per_call_usdc REAL NOT NULL,
            category TEXT NOT NULL,
            api_key_ref TEXT NOT NULL,
            x402_url TEXT NOT NULL,
            analytics_url TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(provider_id) REFERENCES providers(provider_id)
        )
        """
    )

    # Service secrets (encrypted)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS service_secrets (
            service_id TEXT PRIMARY KEY,
            auth_location TEXT NOT NULL,   -- 'header' or 'query'
            auth_key TEXT NOT NULL,        -- e.g., 'Authorization' or 'api_key'
            auth_template TEXT NOT NULL,   -- e.g., 'Bearer {key}' or '{key}'
            api_key_cipher BLOB NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(service_id) REFERENCES services(service_id)
        )
        """
    )

    # ApiCalls
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS api_calls (
            call_id TEXT PRIMARY KEY,
            service_id TEXT NOT NULL,
            tx_hash TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            response_hash TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            payer TEXT,
            amount_paid_usdc REAL,
            FOREIGN KEY(service_id) REFERENCES services(service_id)
        )
        """
    )

    conn.commit()
    cur.close()


def init_db() -> None:
    conn = get_connection()
    try:
        _ensure_schema(conn)
    finally:
        conn.close()

