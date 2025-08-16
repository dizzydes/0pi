from __future__ import annotations

import uuid
from typing import Any, Dict
import json

from fastapi import APIRouter, HTTPException, Path, Request, Response
import requests

from backend.db import get_connection
from backend.hash_utils import canonical_keccak_hex

router = APIRouter(prefix="/x402", tags=["x402"])


# x402 verification integration (seller side)
class X402Error(Exception):
    pass


def _format_price_str(usdc: float) -> str:
    # x402 expects dollar string like "$0.001"
    return f"${usdc:.6f}".rstrip("0").rstrip(".")


def _network() -> str:
    import os
    return os.getenv("X402_NETWORK", os.getenv("X402_CHAIN", "base"))


def _challenge_response(price_usdc: float, pay_to: str, network: str) -> Response:
    # Standards-friendly 402 with payment hints for x402 clients
    headers = {
        "X-402-Price": _format_price_str(price_usdc),
        "X-402-Pay-To": pay_to,
        "X-402-Network": network,
        "Content-Type": "application/json",
    }
    body = {
        "error": "payment_required",
        "price": _format_price_str(price_usdc),
        "pay_to_address": pay_to,
        "network": network,
    }
    return Response(content=json.dumps(body), status_code=402, headers=headers, media_type="application/json")


def verify_or_challenge(req: Request, svc: Dict[str, Any], provider: Dict[str, Any]) -> dict:
    price_usdc = float(svc["price_per_call_usdc"])  # type: ignore
    pay_to = provider["payout_wallet"]  # type: ignore
    net = _network()

    # Attempt to use x402 if available; otherwise, return a 402 challenge
    try:
        import importlib
        # Prefer a generic server verifier if exposed; otherwise raise to fall back to challenge
        x402_mod = importlib.import_module("x402")
        # If the library provides a server-side verifier API, integrate here.
        # Since API surface may vary, we conservatively challenge and let the x402 client retry.
        # Returning a 402 with headers allows x402 clients to fulfill payment automatically.
        return {"payer": None, "amount": 0.0, "ticket_id": None, "challenge": _challenge_response(price_usdc, pay_to, net)}
    except Exception:
        # Library not present or no server API available — send challenge
        return {"payer": None, "amount": 0.0, "ticket_id": None, "challenge": _challenge_response(price_usdc, pay_to, net)}


def emit_onchain_proof(call_id: str, req_hash: str, resp_hash: str) -> str:
    # Emit event via Web3 to ApiCallsRegistry
    import os
    from web3 import Web3
    rpc = os.getenv("BASE_RPC_URL")
    pk = os.getenv("ETH_PRIVATE_KEY")
    contract_addr = os.getenv("REGISTRY_CONTRACT_ADDRESS")
    contract_abi_path = os.getenv("REGISTRY_CONTRACT_ABI", "contracts/ApiCallsRegistry.abi.json")
    if not (rpc and pk and contract_addr):
        # As a fallback (for local dev without deployment), generate deterministic tx-hash-like value
        return "0x" + uuid.uuid5(uuid.NAMESPACE_URL, call_id).hex
    w3 = Web3(Web3.HTTPProvider(rpc))
    acct = w3.eth.account.from_key(pk)
    from pathlib import Path
    abi = json.loads(Path(contract_abi_path).read_text())
    contract = w3.eth.contract(address=Web3.to_checksum_address(contract_addr), abi=abi)
    nonce = w3.eth.get_transaction_count(acct.address)
    tx = contract.functions.emitApiCallProved(call_id, req_hash, resp_hash).build_transaction({
        "from": acct.address,
        "nonce": nonce,
        "chainId": w3.eth.chain_id,
        "gas": 300000,
        "maxFeePerGas": w3.to_wei("1.5", "gwei"),
        "maxPriorityFeePerGas": w3.to_wei("1", "gwei"),
    })
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
    return tx_hash.hex()


def load_service_by_provider_name(conn, provider_name: str) -> Dict[str, Any]:
    """Resolve the latest service row for a given provider name."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.service_id, s.provider_id, s.x402_url, s.price_per_call_usdc
        FROM services s
        JOIN providers p ON p.provider_id = s.provider_id
        WHERE p.provider_name = ?
        ORDER BY s.created_at DESC
        LIMIT 1
        """,
        (provider_name,),
    )
    row = cur.fetchone()
    cur.close()
    if not row:
        raise HTTPException(status_code=404, detail="Service not found for provider")
    return {"service_id": row[0], "provider_id": row[1], "x402_url": row[2], "price_per_call_usdc": row[3]}


def load_service_by_id(conn, service_id: str) -> Dict[str, Any]:
    cur = conn.cursor()
    cur.execute(
        "SELECT service_id, provider_id, x402_url, price_per_call_usdc FROM services WHERE service_id=?",
        (service_id,),
    )
    row = cur.fetchone()
    cur.close()
    if not row:
        raise HTTPException(status_code=404, detail="Service not found")
    return {"service_id": row[0], "provider_id": row[1], "x402_url": row[2], "price_per_call_usdc": row[3]}


# Backward-compat: handle old ID-based path like /x402/svc_abcdefgh
@router.post("/{service_id:svc_[0-9a-fA-F]{8}}")
async def paid_proxy_by_id(service_id: str = Path(...), request: Request = None) -> Response:
    # Hash request body early (also used for proof)
    try:
        body = await request.json()
    except Exception:
        body = None

    conn = get_connection()
    svc = load_service_by_id(conn, service_id)

    # Load provider for payout address
    curp = conn.cursor()
    curp.execute("SELECT provider_id, payout_wallet FROM providers WHERE provider_id=?", (svc["provider_id"],))
    prow = curp.fetchone()
    curp.close()
    if not prow:
        conn.close()
        raise HTTPException(status_code=500, detail="Provider not found for service")
    provider = dict(prow)

    # Issue x402 challenge (or verify if server supports it)
    verification = verify_or_challenge(request, svc, provider)
    if verification.get("challenge") is not None:
        conn.close()
        return verification["challenge"]

    paid = verification

    # Hash request
    req_hash = canonical_keccak_hex(body if body is not None else {})

    call_id = str(uuid.uuid4())

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
        headers = {"Content-Type": "application/json"}
        cur = conn.cursor()
        cur.execute("SELECT auth_location, auth_key, auth_template, api_key_cipher FROM service_secrets WHERE service_id=?", (service_id,))
        sec = cur.fetchone()
        if sec:
            from backend.crypto import decrypt_secret
            key_plain = decrypt_secret(sec[3], aad=service_id.encode("utf-8")).decode("utf-8")
            auth_location = sec[0]
            auth_key = sec[1]
            auth_template = sec[2]
            token_value = auth_template.replace("{key}", key_plain)
            if auth_location == "header":
                headers[auth_key] = token_value
            else:
                if method == "GET":
                    pass
                else:
                    if isinstance(body, dict):
                        body.setdefault(auth_key, token_value)
        if method == "GET":
            params = body or {}
            if sec and sec[0] == "query":
                params = dict(params)
                params[sec[1]] = auth_template.replace("{key}", key_plain)  # type: ignore[name-defined]
            resp = requests.get(upstream, params=params, headers=headers, timeout=30)
        else:
            resp = requests.request(method, upstream, json=body, headers=headers, timeout=30)
    except requests.RequestException as e:
        conn.close()
        raise HTTPException(status_code=502, detail=f"Upstream error: {e}")

    try:
        resp_json = resp.json()
    except Exception:
        resp_json = {"non_json": True, "status_code": resp.status_code}
    resp_hash = canonical_keccak_hex(resp_json)

    try:
        tx_hash = emit_onchain_proof(call_id, req_hash, resp_hash)
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=f"On-chain emission failed: {e}")

    cur = conn.cursor()
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    cur.execute(
        """
        INSERT INTO api_calls (call_id, service_id, tx_hash, request_hash, response_hash, timestamp, payer, amount_paid_usdc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            call_id,
            service_id,
            tx_hash,
            req_hash,
            resp_hash,
            now,
            paid.get("payer"),
            float(paid.get("amount") or 0.0),
        ),
    )
    conn.commit()
    cur.close()
    conn.close()

    import os
    subgraph_base = os.getenv("SUBGRAPH_EXPLORER_BASE", "https://thegraph.com/explorer?query=0pi\u0026search=")
    headers_out = {
        "X-0pi-Call-Id": call_id,
        "X-0pi-Tx-Hash": tx_hash,
        "X-0pi-Subgraph": f"{subgraph_base}{service_id}",
    }
    content = resp.content
    return Response(content=content, status_code=resp.status_code, headers=headers_out, media_type=resp.headers.get("content-type", "application/octet-stream"))


@router.post("/{provider_name}")
async def paid_proxy(provider_name: str = Path(...), request: Request = None) -> Response:
    # Hash request body early (also used for proof)
    try:
        body = await request.json()
    except Exception:
        body = None

    # Lookup latest service for this provider (and provider_id, price)
    conn = get_connection()
    svc = load_service_by_provider_name(conn, provider_name)
    service_id = svc["service_id"]

    # Load provider for payout address
    curp = conn.cursor()
    curp.execute("SELECT provider_id, payout_wallet FROM providers WHERE provider_id=?", (svc["provider_id"],))
    prow = curp.fetchone()
    curp.close()
    if not prow:
        conn.close()
        raise HTTPException(status_code=500, detail="Provider not found for service")
    provider = dict(prow)

    # Issue x402 challenge (or verify if server supports it)
    verification = verify_or_challenge(request, svc, provider)
    if verification.get("challenge") is not None:
        # Return a 402 that x402 clients understand and can satisfy automatically
        conn.close()
        return verification["challenge"]

    paid = verification

    # Hash request
    req_hash = canonical_keccak_hex(body if body is not None else {})

    call_id = str(uuid.uuid4())

    # Load upstream info from MCP listing file created at service creation
    from pathlib import Path
    import json as _json
    listing_path = Path(__file__).resolve().parents[1] / "mcp_listings" / f"{svc['service_id']}.json"
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
        # Include provider secret if present
        headers = {"Content-Type": "application/json"}
        # Load secret
        cur = conn.cursor()
        cur.execute("SELECT auth_location, auth_key, auth_template, api_key_cipher FROM service_secrets WHERE service_id=?", (service_id,))
        sec = cur.fetchone()
        if sec:
            from backend.crypto import decrypt_secret
            key_plain = decrypt_secret(sec[3], aad=service_id.encode("utf-8")).decode("utf-8")
            auth_location = sec[0]
            auth_key = sec[1]
            auth_template = sec[2]
            token_value = auth_template.replace("{key}", key_plain)
            if auth_location == "header":
                headers[auth_key] = token_value
            else:
                # inject into query/body
                if method == "GET":
                    # append to query params below
                    pass
                else:
                    # if JSON body, add field if not present
                    if isinstance(body, dict):
                        body.setdefault(auth_key, token_value)
                    else:
                        # if non-JSON, skip
                        pass
        # Execute request
        if method == "GET":
            params = body or {}
            if sec and sec[0] == "query":
                # ensure auth in query
                params = dict(params)
                params[sec[1]] = auth_template.replace("{key}", key_plain)  # type: ignore[name-defined]
            resp = requests.get(upstream, params=params, headers=headers, timeout=30)
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

    # Emit on-chain event and get tx hash
    try:
        tx_hash = emit_onchain_proof(call_id, req_hash, resp_hash)
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=f"On-chain emission failed: {e}")

    # Log call in SQLite (new schema)
    cur = conn.cursor()
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    cur.execute(
        """
        INSERT INTO api_calls (call_id, service_id, tx_hash, request_hash, response_hash, timestamp, payer, amount_paid_usdc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            call_id,
            service_id,
            tx_hash,
            req_hash,
            resp_hash,
            now,
            paid.get("payer"),
            float(paid.get("amount") or 0.0),
        ),
    )
    conn.commit()
    cur.close()
    conn.close()

    # Build proxied response with headers
    import os
    subgraph_base = os.getenv("SUBGRAPH_EXPLORER_BASE", "https://thegraph.com/explorer?query=0pi&search=")
    headers_out = {
        "X-0pi-Call-Id": call_id,
        "X-0pi-Tx-Hash": tx_hash,
        "X-0pi-Subgraph": f"{subgraph_base}{service_id}",
    }

    content = resp.content
    return Response(content=content, status_code=resp.status_code, headers=headers_out, media_type=resp.headers.get("content-type", "application/octet-stream"))

