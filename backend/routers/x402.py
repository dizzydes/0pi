from __future__ import annotations

import uuid
from typing import Any, Dict
import json

from fastapi import APIRouter, HTTPException, Path, Request, Response
import requests

from backend.db import engine
from backend.crypto import decrypt_secret
from backend.hash_utils import canonical_keccak_hex

router = APIRouter(prefix="/x402", tags=["x402"])


# Placeholder payment validator; replace with Coinbase x402 SDK calls later
class X402Error(Exception):
    pass


def validate_payment(req: Request, service_id: str) -> None:
    # TODO: integrate Coinbase x402 validation
    # For now, accept all requests; raise X402Error on invalid payment
    return


def emit_onchain_proof(call_id: str, req_hash: str, resp_hash: str) -> str:
    # TODO: integrate with Base chain to emit ApiCallProved; return tx hash
    # Placeholder returns deterministic-looking UUID-based hex
    return "0x" + uuid.uuid5(uuid.NAMESPACE_URL, call_id).hex


def load_provider_by_service(conn, service_id: str) -> Dict[str, Any]:
    # Our current service_id is svc_{provider_id}
    if not service_id.startswith("svc_"):
        raise HTTPException(status_code=404, detail="Unknown service_id")
    try:
        provider_id = int(service_id.split("_", 1)[1])
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown service_id")
    row = conn.exec_driver_sql(
        "SELECT id, name, docs_url, api_key_ciphertext FROM providers WHERE id = :id",
        {"id": provider_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Service not found")
    return {"id": row[0], "name": row[1], "docs_url": row[2], "api_key_ciphertext": row[3]}


@router.post("/{service_id}")
async def paid_proxy(service_id: str = Path(...), request: Request = None) -> Response:
    # Payment validation (placeholder)
    try:
        validate_payment(request, service_id)
    except X402Error as e:
        raise HTTPException(status_code=402, detail=str(e))

    # Read JSON body if present; otherwise forward as-is
    try:
        body = await request.json()
    except Exception:
        body = None

    # Hash request
    req_hash = canonical_keccak_hex(body if body is not None else {})

    call_id = str(uuid.uuid4())

    # Lookup provider and decrypt API key
    with engine.begin() as conn:
        provider = load_provider_by_service(conn, service_id)
        api_key_bytes = decrypt_secret(provider["api_key_ciphertext"])  # type: ignore
        api_key = api_key_bytes.decode("utf-8")

    # Proxy to real API: read from endpoints table (first endpoint for provider)
    with engine.begin() as conn:
        ep = conn.exec_driver_sql(
            "SELECT id, path, method FROM endpoints WHERE provider_id = :pid ORDER BY id LIMIT 1",
            {"pid": provider["id"]},
        ).fetchone()
    if not ep:
        raise HTTPException(status_code=500, detail="No upstream endpoint configured")

    endpoint_id, upstream, method = ep[0], ep[1], (ep[2] or "POST").upper()

    try:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        if method == "GET":
            resp = requests.get(upstream, params=body or {}, headers=headers, timeout=30)
        else:
            resp = requests.request(method, upstream, json=body, headers=headers, timeout=30)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Upstream error: {e}")

    # Compute response hash
    try:
        resp_json = resp.json()
    except Exception:
        resp_json = {"non_json": True, "status_code": resp.status_code}
    resp_hash = canonical_keccak_hex(resp_json)

    # Emit on-chain event (stub) and get tx hash
    tx_hash = emit_onchain_proof(call_id, req_hash, resp_hash)

    # Log call in SQLite
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            INSERT INTO api_calls (endpoint_id, user_wallet, request_body, response_hash, onchain_tx_hash)
            VALUES (:endpoint_id, :user_wallet, :request_body, :response_hash, :onchain_tx_hash)
            """,
            {
                "endpoint_id": endpoint_id,
                "user_wallet": "",  # can be taken from payment context later
                "request_body": None if body is None else json.dumps(body),
                "response_hash": resp_hash,
                "onchain_tx_hash": tx_hash,
            },
        )

    # Build proxied response with headers
    headers_out = {
        "X-0pi-Call-Id": call_id,
        "X-0pi-Tx-Hash": tx_hash,
        "X-0pi-Subgraph": f"https://thegraph.com/explorer?query=0pi&service_id={service_id}",
    }

    content = resp.content
    return Response(content=content, status_code=resp.status_code, headers=headers_out, media_type=resp.headers.get("content-type", "application/octet-stream"))

