from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv
from sqlalchemy import create_engine
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

