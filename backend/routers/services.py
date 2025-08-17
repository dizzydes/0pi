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

# Allowed categories for services (server-enforced)
ALLOWED_CATEGORIES: list[str] = [
    "🌍 Geo & Mapping",
    "⛅ Weather & Environment",
    "🚖 Transportation",
    "💬 Language & Chat AI",
    "👁️ Vision AI",
    "🎙️ Speech AI",
    "🎯 Recommendation & Personalization",
    "🔍 Search",
    "💳 Finance & Payments",
    "🛒 E-commerce",
    "📊 CRM & Sales",
    "📋 Project Management",
    "💬 Messaging",
    "✉️ Email",
    "📱 Social Media",
    "📰 News & Content",
    "📚 Knowledge Bases",
    "📈 Market Data",
    "☁️ Cloud & Storage",
    "🔐 Authentication & Identity",
    "🛠️ DevOps & Monitoring",
    "🌐 Translation",
    "📆 Calendar & Scheduling",
    "📑 Document & Data Management",
]


class ServiceCreate(BaseModel):
    provider_name: str  # used in /api/{provider_name}/...
    provider_id: str | None = None
    cdp_wallet_id: str | None = None  # optional when not using CDP
    api_docs_url: HttpUrl  # link to docs for reference
    price_per_call_usdc: float = Field(ge=0)
    payout_wallet: str  # Provider payout EVM address
    category: str
    upstream_base_url: HttpUrl  # e.g., https://api.openai.com

    # Optional auth details for injecting provider API key
    auth_location: str | None = Field(default="header", description="header or query")
    auth_key: str | None = Field(default="Authorization")
    auth_template: str | None = Field(default="Bearer {key}")
    api_key_plain: str | None = Field(default=None, description="Provider API key to encrypt and store for proxy injection")

    @validator("provider_name")
    def _provider_name_slug(cls, v: str) -> str:
        import re
        if not re.fullmatch(r"[a-z0-9_]+", v):
            raise ValueError("must be lowercase, one word, digits/underscores only (e.g., openai, weather_api)")
        return v

    @validator("category")
    def _category_allowed(cls, v: str) -> str:
        if v not in ALLOWED_CATEGORIES:
            raise ValueError("category must be one of the allowed values")
        return v


@router.get("")
def list_services() -> List[dict]:
    # Placeholder; later read from DB
    return []


@router.post("")
def create_service(payload: ServiceCreate) -> dict:
    # Ensure schema exists
    init_db()

    now = datetime.now(timezone.utc).isoformat()

    # Generate IDs
    service_id = f"svc_{uuid.uuid4().hex[:8]}"
    # Default deterministic provider_id derived from provider_name
    provider_id = f"prov_{payload.provider_name}"

    # Expose x402 endpoint by provider_name (human-friendly) instead of opaque service_id
    x402_url = f"/x402/{payload.provider_name}"
    # Use configurable subgraph URL base if available
    import os
    subgraph_base = os.getenv("SUBGRAPH_EXPLORER_BASE", "https://thegraph.com/explorer?query=0pi&search=")
    analytics_url = f"{subgraph_base}{service_id}"

    # Persist provider and service using sqlite3
    conn = get_connection()
    cur = conn.cursor()

    try:
        # Prefer existing provider by name (legacy rows may use random provider_id)
        cur.execute("SELECT provider_id FROM providers WHERE provider_name = ?", (payload.provider_name,))
        row = cur.fetchone()
        if row and row[0]:
            provider_id = row[0]
        else:
            # Ensure provider row exists with deterministic provider_id
            cur.execute(
                """
                INSERT OR IGNORE INTO providers (provider_id, provider_name, cdp_wallet_id, payout_wallet, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    provider_id,
                    payload.provider_name,
                    (payload.cdp_wallet_id or ""),
                    payload.payout_wallet,
                    now,
                ),
            )
        # Keep payout wallet up to date for the chosen provider_id
        cur.execute(
            """
            UPDATE providers SET payout_wallet = ?, cdp_wallet_id = COALESCE(NULLIF(?, ''), cdp_wallet_id)
            WHERE provider_id = ?
            """,
            (
                payload.payout_wallet,
                (payload.cdp_wallet_id or ""),
                provider_id,
            ),
        )

        # Save MCP listing JSON (holds upstream info for proxy)
        listing = {
            "id": service_id,
            "provider_id": provider_id,
            "name": payload.provider_name,
            "category": payload.category,
            "price_per_call_usdc": payload.price_per_call_usdc,
            "docs_url": str(payload.api_docs_url),
            "upstream_base_url": str(payload.upstream_base_url),
            # API carry-through base path for this service
            "api_base": f"/api/{payload.provider_name}",
            "x402_url": x402_url,
        }
        out_path = MCP_DIR / f"{service_id}.json"
        out_path.write_text(json.dumps(listing, indent=2))

        # Insert service row
        cur.execute(
            """
            INSERT INTO services (service_id, provider_id, api_docs_url, price_per_call_usdc, category, upstream_base_url, x402_url, analytics_url, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                service_id,
                provider_id,
                str(payload.api_docs_url),
                float(payload.price_per_call_usdc),
                payload.category,
                str(payload.upstream_base_url),
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
        "api_base": f"/api/{payload.provider_name}",
        "analytics_url": analytics_url,
        "mcp_listing_path": str(out_path),
        "status": "created",
    }

