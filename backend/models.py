from __future__ import annotations

from pydantic import BaseModel
from typing import Optional

# Minimal models to align with your schema; using simple dicts for now.

class Provider(BaseModel):
    id: Optional[int] = None
    name: Optional[str] = None
    ens_name: str
    wallet_address: str
    api_key_ciphertext: bytes
    price_per_call: float
    docs_url: Optional[str] = None

class Endpoint(BaseModel):
    id: Optional[int] = None
    provider_id: int
    path: str
    method: str
    description: Optional[str] = None
    mcp_schema: Optional[str] = None
    openapi_schema: Optional[str] = None

class APICall(BaseModel):
    id: Optional[int] = None
    endpoint_id: int
    user_wallet: str
    request_body: Optional[str] = None
    response_hash: str
    onchain_tx_hash: Optional[str] = None

