from __future__ import annotations

from typing import List
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, HttpUrl

from backend.db import init_sqlite_schema, engine
from backend.crypto import encrypt_secret

router = APIRouter(prefix="/services", tags=["services"])

MCP_DIR = Path(__file__).resolve().parents[1] / "mcp_listings"
MCP_DIR.mkdir(parents=True, exist_ok=True)


class ServiceCreate(BaseModel):
    provider_name: str
    api_docs_url: HttpUrl
    price_per_call_usdc: float
    payout_wallet: str  # Coinbase Embedded Wallet ID
    api_key: str        # secure ref, stored encrypted
    category: str


@router.get("")
def list_services() -> List[dict]:
    # Placeholder; later read from DB
    return []


@router.post("")
def create_service(payload: ServiceCreate) -> dict:
    # Ensure schema exists
    init_sqlite_schema()

    # Encrypt API key (store only ciphertext)
    ciphertext = encrypt_secret(payload.api_key.encode("utf-8"))

    # Persist provider and endpoint basics using SQLAlchemy Core
    with engine.begin() as conn:
        # Insert provider
        res = conn.exec_driver_sql(
            """
            INSERT INTO providers (name, ens_name, wallet_address, api_key_ciphertext, price_per_call, docs_url, payout_wallet, category)
            VALUES (:name, :ens, :wallet, :ct, :price, :docs, :payout, :category)
            """,
            {
                "name": payload.provider_name,
                "ens": payload.provider_name,  # placeholder, can collect separately
                "wallet": payload.payout_wallet,  # reuse as wallet holder for now
                "ct": ciphertext,
                "price": payload.price_per_call_usdc,
                "docs": str(payload.api_docs_url),
                "payout": payload.payout_wallet,
                "category": payload.category,
            },
        )
        provider_id = res.lastrowid

        # Create a simple service_id (use provider_id for now)
        service_id = f"svc_{provider_id}"

        # x402 proxy URL (placeholder composition)
        x402_url = f"/x402/{service_id}"

        # Save MCP listing JSON
        listing = {
            "id": service_id,
            "name": payload.provider_name,
            "category": payload.category,
            "price_per_call_usdc": payload.price_per_call_usdc,
            "docs_url": str(payload.api_docs_url),
            "x402_url": x402_url,
        }
        out_path = MCP_DIR / f"{service_id}.json"
        out_path.write_text(json.dumps(listing, indent=2))

    # Return completion payload
    analytics_url = f"https://thegraph.com/explorer?query=0pi&service_id={service_id}"
    return {
        "service_id": service_id,
        "x402_url": x402_url,
        "api_endpoint": x402_url,
        "analytics_url": analytics_url,
        "mcp_listing_path": str(out_path),
        "status": "created",
    }

