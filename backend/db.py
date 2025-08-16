from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# Load .env
load_dotenv(override=False)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "db.sqlite"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")

engine: Engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)


def apply_sqlite_pragmas(engine: Engine) -> None:
    if not str(DATABASE_URL).startswith("sqlite"):
        return
    with engine.begin() as c:
        c.exec_driver_sql("PRAGMA journal_mode=WAL")
        c.exec_driver_sql("PRAGMA synchronous=NORMAL")
        c.exec_driver_sql("PRAGMA foreign_keys=ON")


# Ensure folder exists
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
apply_sqlite_pragmas(engine)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS providers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT,
  ens_name TEXT,
  wallet_address TEXT,
  api_key_ciphertext BLOB NOT NULL,
  price_per_call NUMERIC(10,2) NOT NULL,
  docs_url TEXT,
  payout_wallet TEXT,
  category TEXT
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
    # Use raw connection executescript to run multiple statements
    raw = engine.raw_connection()
    try:
        raw.executescript(SCHEMA_SQL)
        raw.commit()
    finally:
        raw.close()

