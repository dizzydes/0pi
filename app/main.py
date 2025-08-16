from __future__ import annotations

from fastapi import FastAPI

from app.db import engine, init_sqlite_schema, apply_sqlite_pragmas

app = FastAPI(title="0pi")


@app.on_event("startup")
def startup() -> None:
    # Ensure pragmas and schema are applied on app start
    apply_sqlite_pragmas(engine)
    init_sqlite_schema()


@app.get("/")
def health():
    return {"status": "ok"}

