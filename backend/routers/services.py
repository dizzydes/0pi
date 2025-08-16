from __future__ import annotations

from typing import List
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, HttpUrl

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
    price_per_call_usdc: float
    payout_wallet: str  # Coinbase Embedded Wallet ID
    api_key_ref: str    # secure ref; not stored as ciphertext in DB per spec
    category: str


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
    analytics_url = f"https://thegraph.com/explorer?query=0pi&service_id={service_id}"

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

    conn.commit()
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

