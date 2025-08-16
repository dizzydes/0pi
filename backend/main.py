from __future__ import annotations

import os
from pathlib import Path
import logging

from fastapi import FastAPI

# Load environment variables early from project root
try:
    from dotenv import load_dotenv  # type: ignore
    project_root = Path(__file__).resolve().parent.parent
    root_env = project_root / ".env"
    root_env_local = project_root / ".env.local"

    if root_env.exists():
        load_dotenv(dotenv_path=root_env, override=False)
    if root_env_local.exists():
        # local overrides base .env
        load_dotenv(dotenv_path=root_env_local, override=True)
except Exception:
    # If python-dotenv is not installed or loading fails, proceed without raising here.
    pass

from backend.db import init_db
from backend.routers.services import router as services_router
from backend.routers.calls import router as calls_router
from backend.routers.x402 import router as x402_router
from backend.routers.admin import router as admin_router
from backend.routers.factory import router as factory_router
from backend.routers.cdp import router as cdp_router

logger = logging.getLogger(__name__)

app = FastAPI(title="0pi-backend")


@app.on_event("startup")
def startup() -> None:
    # Initialize SQLite schema
    init_db()

    # Configure CDP SDK if available and env vars are set
    api_key_name = os.getenv("CDP_API_KEY_NAME")
    api_key_pk = os.getenv("CDP_API_KEY_PRIVATE_KEY")
    if api_key_name and api_key_pk:
        try:
            import cdp  # type: ignore
            if hasattr(cdp, "Cdp"):
                cdp.Cdp.configure(api_key_name, api_key_pk)  # type: ignore[attr-defined]
                logger.info("CDP SDK configured via Cdp.configure (NAME/PRIVATE_KEY)")
            elif hasattr(cdp, "configure"):
                getattr(cdp, "configure")(api_key_name, api_key_pk)  # type: ignore[misc]
                logger.info("CDP SDK configured via configure() (NAME/PRIVATE_KEY)")
            else:
                logger.info("CDP SDK detected but exposes neither Cdp nor configure; skipping startup configure")
        except Exception as e:
            logger.warning(f"CDP SDK configuration failed: {e.__class__.__name__}: {e}")
    else:
        logger.info("CDP SDK not configured: missing CDP_API_KEY_NAME or CDP_API_KEY_PRIVATE_KEY")


@app.get("/")
def health():
    return {"status": "ok", "service": "backend"}


# Routers
app.include_router(services_router)
app.include_router(calls_router)
app.include_router(x402_router)
app.include_router(admin_router)
app.include_router(factory_router)
app.include_router(cdp_router)

