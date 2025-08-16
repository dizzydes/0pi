from __future__ import annotations

from typing import List
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, HttpUrl, Field, validator

from backend.db import get_connection, init_db
from datetime import datetime, timezone
import uuid

router = APIRouter(prefix="/services", tags=["services"])

MCP_DIR = Path(__file__).resolve().parents[1] / "mcp_listings"
MCP_DIR.mkdir(parents=True, exist_ok=True)


class ServiceCreate(BaseModel):
    provider_name: str
    provider_id: str | None = None
    cdp_wallet_id: str
    api_docs_url: HttpUrl
    upstream_url: HttpUrl  # real API endpoint to proxy (stored in MCP listing)
    method: str = "POST"   # HTTP method for upstream (stored in MCP listing)
    price_per_call_usdc: float = Field(ge=0)
    payout_wallet: str  # Coinbase Embedded Wallet ID
    api_key_ref: str    # external ref for the key (kept for compatibility)
    category: str

    # Optional auth details for injecting provider API key
    auth_location: str | None = Field(default="header", description="header or query")
    auth_key: str | None = Field(default="Authorization")
    auth_template: str | None = Field(default="Bearer {key}")
    api_key_plain: str | None = Field(default=None, description="Provider API key to encrypt and store for proxy injection")

    @validator("method")
    def _method_upper(cls, v: str) -> str:
        mv = v.upper()
        allowed = {"GET", "POST", "PUT", "PATCH", "DELETE"}
        if mv not in allowed:
            raise ValueError(f"method must be one of {sorted(allowed)}")
        return mv


@router.get("")
def list_services() -> List[dict]:
    # Placeholder; later read from DB
    return []


@router.post("")
def create_service(payload: ServiceCreate) -> dict:
    # Ensure schema exists
    init_db()

    now = datetime.now(timezone.utc).isoformat()

    # Generate IDs if not provided
    provider_id = payload.provider_id or f"prov_{uuid.uuid4().hex[:8]}"
    service_id = f"svc_{uuid.uuid4().hex[:8]}"

    x402_url = f"/x402/{service_id}"
    # Use configurable subgraph URL base if available
    import os
    subgraph_base = os.getenv("SUBGRAPH_EXPLORER_BASE", "https://thegraph.com/explorer?query=0pi&search=")
    analytics_url = f"{subgraph_base}{service_id}"

    # Save MCP listing JSON (holds upstream info for proxy)
    listing = {
        "id": service_id,
        "provider_id": provider_id,
        "name": payload.provider_name,
        "category": payload.category,
        "price_per_call_usdc": payload.price_per_call_usdc,
        "docs_url": str(payload.api_docs_url),
        "upstream_url": str(payload.upstream_url),
        "method": payload.method.upper(),
        "x402_url": x402_url,
    }
    out_path = MCP_DIR / f"{service_id}.json"
    out_path.write_text(json.dumps(listing, indent=2))

    # Persist provider and service using sqlite3
    conn = get_connection()
    cur = conn.cursor()

    try:
        # Upsert-like behavior for providers: insert if not exists
        cur.execute("SELECT 1 FROM providers WHERE provider_id=?", (provider_id,))
        exists = cur.fetchone() is not None
        if not exists:
            cur.execute(
                """
                INSERT INTO providers (provider_id, provider_name, cdp_wallet_id, payout_wallet, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    provider_id,
                    payload.provider_name,
                    payload.cdp_wallet_id,
                    payload.payout_wallet,
                    now,
                ),
            )

        # Insert service row
        cur.execute(
            """
            INSERT INTO services (service_id, provider_id, api_docs_url, price_per_call_usdc, category, api_key_ref, x402_url, analytics_url, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                service_id,
                provider_id,
                str(payload.api_docs_url),
                float(payload.price_per_call_usdc),
                payload.category,
                payload.api_key_ref,
                x402_url,
                analytics_url,
                now,
            ),
        )

        # Insert encrypted secret if provided
        if payload.api_key_plain:
            from backend.crypto import encrypt_secret
            cipher = encrypt_secret(payload.api_key_plain.encode("utf-8"), aad=service_id.encode("utf-8"))
            auth_location = (payload.auth_location or "header").lower()
            if auth_location not in ("header", "query"):
                raise HTTPException(status_code=400, detail="auth_location must be 'header' or 'query'")
            auth_key = payload.auth_key or ("Authorization" if auth_location == "header" else "api_key")
            auth_template = payload.auth_template or ("Bearer {key}" if auth_location == "header" else "{key}")
            cur.execute(
                """
                INSERT INTO service_secrets (service_id, auth_location, auth_key, auth_template, api_key_cipher, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (service_id, auth_location, auth_key, auth_template, cipher, now),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        # Remove listing file if we failed
        try:
            out_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    finally:
        cur.close()
        conn.close()

    # Return completion payload
    return {
        "service_id": service_id,
        "x402_url": x402_url,
        "api_endpoint": x402_url,
        "analytics_url": analytics_url,
        "mcp_listing_path": str(out_path),
        "status": "created",
    }

