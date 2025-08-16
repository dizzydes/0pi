from __future__ import annotations

import uuid
from typing import Any, Dict
import json

from fastapi import APIRouter, HTTPException, Path, Request, Response
import requests

from backend.db import get_connection
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


def load_service(conn, service_id: str) -> Dict[str, Any]:
    cur = conn.cursor()
    cur.execute("SELECT service_id, provider_id, x402_url FROM services WHERE service_id=?", (service_id,))
    row = cur.fetchone()
    cur.close()
    if not row:
        raise HTTPException(status_code=404, detail="Service not found")
    return dict(row)


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

    # Lookup service (and provider_id)
    conn = get_connection()
    svc = load_service(conn, service_id)

    # Load upstream info from MCP listing file created at service creation
    from pathlib import Path
    import json as _json
    listing_path = Path(__file__).resolve().parents[1] / "mcp_listings" / f"{service_id}.json"
    if not listing_path.exists():
        conn.close()
        raise HTTPException(status_code=500, detail="MCP listing missing for service")
    listing = _json.loads(listing_path.read_text())
    upstream = listing.get("upstream_url")
    method = (listing.get("method") or "POST").upper()
    if not upstream:
        conn.close()
        raise HTTPException(status_code=500, detail="No upstream endpoint configured")

    try:
        # For now, we don't include provider secrets from DB; api_key_ref is an external ref
        headers = {"Content-Type": "application/json"}
        if method == "GET":
            resp = requests.get(upstream, params=body or {}, headers=headers, timeout=30)
        else:
            resp = requests.request(method, upstream, json=body, headers=headers, timeout=30)
    except requests.RequestException as e:
        conn.close()
        raise HTTPException(status_code=502, detail=f"Upstream error: {e}")

    # Compute response hash
    try:
        resp_json = resp.json()
    except Exception:
        resp_json = {"non_json": True, "status_code": resp.status_code}
    resp_hash = canonical_keccak_hex(resp_json)

    # Emit on-chain event (stub) and get tx hash
    tx_hash = emit_onchain_proof(call_id, req_hash, resp_hash)

    # Log call in SQLite (new schema)
    cur = conn.cursor()
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    cur.execute(
        """
        INSERT INTO api_calls (call_id, service_id, tx_hash, request_hash, response_hash, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            call_id,
            service_id,
            tx_hash,
            req_hash,
            resp_hash,
            now,
        ),
    )
    conn.commit()
    cur.close()
    conn.close()

    # Build proxied response with headers
    headers_out = {
        "X-0pi-Call-Id": call_id,
        "X-0pi-Tx-Hash": tx_hash,
        "X-0pi-Subgraph": f"https://thegraph.com/explorer?query=0pi&service_id={service_id}",
    }

    content = resp.content
    return Response(content=content, status_code=resp.status_code, headers=headers_out, media_type=resp.headers.get("content-type", "application/octet-stream"))

