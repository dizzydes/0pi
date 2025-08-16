from __future__ import annotations

from fastapi import FastAPI

from backend.db import engine, apply_sqlite_pragmas
from backend.routers.services import router as services_router
from backend.routers.calls import router as calls_router
from backend.routers.x402 import router as x402_router

app = FastAPI(title="0pi-backend")


@app.on_event("startup")
def startup() -> None:
    # Ensure pragmas are set and DB file exists
    apply_sqlite_pragmas(engine)


@app.get("/")
def health():
    return {"status": "ok", "service": "backend"}


# Routers
app.include_router(services_router)
app.include_router(calls_router)
app.include_router(x402_router)

