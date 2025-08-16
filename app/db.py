from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# Load .env at import time for CLI scripts
load_dotenv(override=False)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///0pi.db")

# Create engine
engine: Engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)


def apply_sqlite_pragmas(engine: Engine) -> None:
    """
    Apply recommended SQLite PRAGMAs for dev reliability.
    No-ops on non-sqlite URLs.
    """
    if not DATABASE_URL.startswith("sqlite"):
        return
    with engine.begin() as c:
        c.exec_driver_sql("PRAGMA journal_mode=WAL")
        c.exec_driver_sql("PRAGMA synchronous=NORMAL")
        c.exec_driver_sql("PRAGMA foreign_keys=ON")


@contextmanager
def engine_connection() -> Iterator:
    with engine.begin() as conn:
        yield conn


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS providers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT,
  ens_name TEXT NOT NULL,
  wallet_address TEXT NOT NULL,
  api_key_ciphertext BLOB NOT NULL,
  price_per_call NUMERIC(10,2) NOT NULL,
  docs_url TEXT
);

CREATE TABLE IF NOT EXISTS endpoints (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider_id INTEGER REFERENCES providers(id),
  path TEXT NOT NULL,
  method TEXT NOT NULL,
  description TEXT,
  mcp_schema TEXT,
  openapi_schema TEXT
);

CREATE TABLE IF NOT EXISTS api_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  endpoint_id INTEGER REFERENCES endpoints(id),
  user_wallet TEXT NOT NULL,
  request_body TEXT,
  response_hash TEXT NOT NULL,
  onchain_tx_hash TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_endpoints_provider ON endpoints(provider_id);
CREATE INDEX IF NOT EXISTS idx_calls_endpoint_time ON api_calls(endpoint_id, created_at);
CREATE INDEX IF NOT EXISTS idx_calls_tx ON api_calls(onchain_tx_hash);
""".strip()


def init_sqlite_schema() -> None:
    """Initialize the SQLite schema.

    For SQLite, use executescript so multiple statements run in one call.
    For other engines, fall back to simple semicolon splitting.
    """
    if DATABASE_URL.startswith("sqlite"):
        # Use raw DBAPI connection to access executescript
        raw = engine.raw_connection()
        try:
            raw.executescript(SCHEMA_SQL)
            raw.commit()
        finally:
            raw.close()
    else:
        with engine.begin() as conn:
            for stmt in SCHEMA_SQL.split(";"):
                s = stmt.strip()
                if s:
                    conn.execute(text(s))


# One-time pragma and schema application on import for convenience in scripts
apply_sqlite_pragmas(engine)


