from __future__ import annotations

import uuid
from typing import Any, Dict
import json

from fastapi import APIRouter, HTTPException, Path, Request, Response
import requests
import logging

from backend.db import get_connection
from backend.hash_utils import canonical_keccak_hex

router = APIRouter(prefix="/x402", tags=["x402"]) 
logger = logging.getLogger(__name__)


# x402 verification integration (seller side)
class X402Error(Exception):
    pass


def _verify_receipt(receipt_header: str, *, expected_amount: float, expected_pay_to: str, network: str) -> Dict[str, Any] | None:
    """
    Production-style receipt verifier.

    Expects a JSON object in the receipt header with keys:
    { payer, amount, ticket_id, pay_to, network }

    Verifies on-chain (Base) that ticket_id (a tx hash) executed a USDC transfer
    of at least `expected_amount` to `expected_pay_to` and that the chain matches `network`.

    Env required:
      - BASE_RPC_URL: HTTPS RPC endpoint for Base
      - USDC_CONTRACT_ADDRESS: USDC token address on Base
    """
    import os, json as _json
    from web3 import Web3

    logger.info("x402.verify: start parsing receipt header")
    try:
        data = _json.loads(receipt_header)
        if not isinstance(data, dict):
            logger.warning("x402.verify: receipt not a JSON object")
            return None
    except Exception as e:
        logger.warning(f"x402.verify: invalid JSON in receipt: {e}")
        return None

    # Quick field checks
    try:
        declared_amount = float(data.get("amount") or 0.0)
    except Exception:
        logger.warning("x402.verify: amount not numeric")
        return None
    pay_to_hdr = (data.get("pay_to") or "").strip()
    network_hdr = (data.get("network") or "").strip().lower()
    tx_hash = (data.get("ticket_id") or data.get("id") or "").strip()
    payer_hdr = (data.get("payer") or "").strip()

    logger.info(f"x402.verify: parsed amount={declared_amount} pay_to={pay_to_hdr} network={network_hdr} tx={tx_hash[:10]}…")

    if declared_amount < expected_amount:
        logger.warning(f"x402.verify: amount too low: declared={declared_amount} expected>={expected_amount}")
        return None
    if pay_to_hdr.lower() != expected_pay_to.lower():
        logger.warning(f"x402.verify: pay_to mismatch: header={pay_to_hdr} expected={expected_pay_to}")
        return None
    if network_hdr != network.lower():
        logger.warning(f"x402.verify: network mismatch: header={network_hdr} expected={network}")
        return None
    if not tx_hash or not tx_hash.startswith("0x"):
        logger.warning("x402.verify: ticket_id missing or not 0x-prefixed")
        return None

    # On-chain verification
    rpc = os.getenv("BASE_RPC_URL")
    usdc_addr_env = os.getenv("USDC_CONTRACT_ADDRESS")
    if not rpc or not usdc_addr_env:
        logger.error("x402.verify: missing BASE_RPC_URL or USDC_CONTRACT_ADDRESS; failing closed")
        return None

    try:
        w3 = Web3(Web3.HTTPProvider(rpc))
        # Confirm on Base
        chain_id = w3.eth.chain_id
        logger.info(f"x402.verify: connected chain_id={chain_id}")
        if network.lower() == "base" and chain_id != 8453:
            logger.warning("x402.verify: RPC not on Base mainnet (8453)")
            return None

        receipt = w3.eth.get_transaction_receipt(tx_hash)
        if not receipt:
            logger.warning("x402.verify: no receipt for tx")
            return None
        if getattr(receipt, 'status', None) != 1:
            logger.warning("x402.verify: tx status != 1")
            return None

        usdc_addr = Web3.to_checksum_address(usdc_addr_env)
        transfer_sig = Web3.keccak(text="Transfer(address,address,uint256)")
        value_transferred = 0
        to_matched = False
        payer_onchain = payer_hdr

        for log in receipt.logs or []:
            if getattr(log, 'address', None) == usdc_addr and getattr(log, 'topics', None) and log.topics[0] == transfer_sig:
                try:
                    to_addr = Web3.to_checksum_address("0x" + log.topics[2].hex()[-40:])
                except Exception:
                    to_addr = ""
                try:
                    from_addr = Web3.to_checksum_address("0x" + log.topics[1].hex()[-40:])
                except Exception:
                    from_addr = ""
                # Parse 32-byte amount from log.data (can be HexBytes or hex str)
                raw = 0
                try:
                    from hexbytes import HexBytes  # type: ignore
                except Exception:
                    HexBytes = bytes  # fallback type hinting
                try:
                    if isinstance(log.data, (bytes, bytearray)):
                        raw = int.from_bytes(log.data, byteorder="big")
                    elif 'HexBytes' in str(type(log.data)):
                        # duck-typing for HexBytes
                        try:
                            raw = int.from_bytes(bytes(log.data), byteorder="big")
                        except Exception:
                            raw = int(getattr(log.data, 'hex', lambda: '0x0')(), 16)
                    else:
                        raw = int(str(log.data), 16)
                except Exception:
                    raw = 0
                logger.info(f"x402.verify: Transfer log from={from_addr} to={to_addr} value_raw={raw}")
                if to_addr.lower() == expected_pay_to.lower():
                    to_matched = True
                    value_transferred += raw
                    payer_onchain = from_addr or payer_onchain

        required_raw = int(round(expected_amount * 10**6))
        logger.info(f"x402.verify: sum_to_pay_to_raw={value_transferred} required_raw={required_raw}")
        if not to_matched or value_transferred < required_raw:
            logger.warning("x402.verify: insufficient transfer to pay_to in tx")
            return None

        logger.info("x402.verify: success")
        return {
            "payer": payer_onchain,
            "amount": declared_amount,
            "ticket_id": tx_hash,
            "pay_to": pay_to_hdr,
            "network": network_hdr,
        }
    except Exception as e:
        logger.exception(f"x402.verify: exception during on-chain verification: {e}")
        return None


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

    logger.info(f"x402: verify_or_challenge price={price_usdc} pay_to={pay_to} net={net}")

    # Server-side verification path: only proceed when a verifiable receipt is present
    # This is production-style: we do not accept unsigned receipts unless explicitly allowed.
    import os
    receipt = req.headers.get("x-402-receipt") or req.headers.get("X-402-Receipt")
    logger.info(f"x402: receipt present={bool(receipt)}")
    if receipt:
        try:
            verified = _verify_receipt(receipt, expected_amount=price_usdc, expected_pay_to=pay_to, network=net)
            if verified:
                logger.info(f"x402: receipt verified for tx={str(verified.get('ticket_id'))[:10]}… payer={verified.get('payer')}")
                return {
                    "payer": verified.get("payer"),
                    "amount": float(verified.get("amount") or 0.0),
                    "ticket_id": verified.get("ticket_id"),
                    "challenge": None,
                }
            else:
                logger.warning("x402: receipt failed verification; issuing 402 challenge")
        except Exception as e:
            logger.exception(f"x402: exception during receipt verification: {e}")
            # fall through to challenge on any verifier error
            pass

    # Optionally allow unsigned receipts for local/dev if explicitly enabled
    if os.getenv("X402_ACCEPT_UNSIGNED", "false").lower() in ("1", "true", "yes") and receipt:
        try:
            import json as _json
            data = _json.loads(receipt)
            amt = float(data.get("amount") or 0.0)
            if amt >= price_usdc and (data.get("pay_to") or "").lower() == pay_to.lower() and (data.get("network") or "").lower() == net.lower():
                logger.info("x402: dev unsigned receipt accepted due to X402_ACCEPT_UNSIGNED=true")
                return {
                    "payer": data.get("payer"),
                    "amount": amt,
                    "ticket_id": data.get("ticket_id") or data.get("id"),
                    "challenge": None,
                }
        except Exception as e:
            logger.warning(f"x402: dev unsigned receipt parse error: {e}")

    # Default: return a 402 challenge with x402 hints
    logger.info("x402: returning 402 challenge")
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
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
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
async def paid_proxy_by_id(request: Request, service_id: str = Path(...)) -> Response:
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
    provider = {"provider_id": prow[0], "payout_wallet": prow[1]}

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


@router.api_route("/{provider_name}/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"]) 
async def paid_proxy_by_provider(request: Request, provider_name: str, full_path: str = "") -> Response:
    # Resolve latest service for provider
    conn = get_connection()
    svc = load_service_by_provider_name(conn, provider_name)

    # Load provider for payout
    curp = conn.cursor()
    curp.execute("SELECT provider_id, payout_wallet FROM providers WHERE provider_id=?", (svc["provider_id"],))
    prow = curp.fetchone()
    curp.close()
    if not prow:
        conn.close()
        raise HTTPException(status_code=500, detail="Provider not found for service")
    provider = {"provider_id": prow[0], "payout_wallet": prow[1]}

    # Issue x402 challenge or verify receipt
    verification = verify_or_challenge(request, svc, provider)
    if verification.get("challenge") is not None:
        conn.close()
        return verification["challenge"]

    paid = verification

    # Build upstream URL from MCP listing
    from pathlib import Path as _Path
    import json as _json
    listing_path = _Path(__file__).resolve().parents[1] / "mcp_listings" / f"{svc['service_id']}.json"
    try:
        logger.info(f"x402: provider={provider_name} service_id={svc['service_id']} listing_path={listing_path}")
    except Exception:
        pass
    if not listing_path.exists():
        conn.close()
        raise HTTPException(status_code=500, detail="MCP listing missing for service")
    listing = _json.loads(listing_path.read_text())
    upstream_base = (listing.get("upstream_base_url") or "").rstrip("/")
    if not upstream_base:
        docs = listing.get("docs_url") or ""
        from urllib.parse import urlparse
        u = urlparse(docs)
        if not (u.scheme and u.netloc):
            conn.close()
            raise HTTPException(status_code=500, detail="No upstream_base_url/docs host configured")
        upstream_base = f"{u.scheme}://{u.netloc}"
    upstream_url = upstream_base + "/" + full_path.lstrip("/")
    try:
        logger.info(f"x402: upstream_url={upstream_url}")
    except Exception:
        pass

    # Prepare outbound request
    method = (request.method if request else "POST").upper()
    headers = {}
    # Copy headers except hop-by-hop and authorization (we will inject from secret)
    for k, v in (request.headers.items() if request else []):
        lk = k.lower()
        if lk in {"host", "content-length", "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade", "authorization"}:
            continue
        headers[k] = v
    headers.setdefault("Accept", "application/json")
    headers.setdefault("Accept-Encoding", "identity")

    # Load and inject secret
    cur = conn.cursor()
    cur.execute("SELECT auth_location, auth_key, auth_template, api_key_cipher FROM service_secrets WHERE service_id=? ORDER BY rowid DESC LIMIT 1", (svc["service_id"],))
    sec = cur.fetchone()
    auth_location = auth_key = auth_template = None
    key_plain = None
    if sec:
        from backend.crypto import decrypt_secret
        auth_location, auth_key, auth_template = sec[0], sec[1], sec[2]
        try:
            key_plain = decrypt_secret(sec[3], aad=svc["service_id"].encode("utf-8")).decode("utf-8")
        except Exception:
            key_plain = None
    cur.close()

    # Body/params and auth injection
    params = {}
    body_json = None
    raw_body = None
    if method == "GET":
        params = dict(request.query_params) if request else {}
        if key_plain and auth_location == "query":
            params[auth_key or "api_key"] = (auth_template or "{key}").replace("{key}", key_plain)
        if key_plain and (auth_location or "header").lower() == "header":
            headers[auth_key or "Authorization"] = (auth_template or "Bearer {key}").replace("{key}", key_plain)
    else:
        # Try to parse JSON; fallback to raw
        try:
            body_json = await request.json() if request else None
        except Exception:
            body_json = None
        if body_json is None and request is not None:
            raw_body = await request.body()
        if key_plain and (auth_location or "header").lower() == "header":
            headers[auth_key or "Authorization"] = (auth_template or "Bearer {key}").replace("{key}", key_plain)
        elif key_plain and auth_location == "query":
            params = dict(request.query_params) if request else {}
            params[auth_key or "api_key"] = (auth_template or "{key}").replace("{key}", key_plain)

    # Hash request canonical form
    try:
        payload_for_hash = body_json if method != "GET" else params
    except Exception:
        payload_for_hash = {}
    req_hash = canonical_keccak_hex(payload_for_hash or {})

    # Perform upstream call
    try:
        if method == "GET":
            resp = requests.get(upstream_url, params=params, headers=headers, timeout=30)
        else:
            if body_json is not None:
                headers.setdefault("Content-Type", "application/json")
                resp = requests.request(method, upstream_url, json=body_json, params=params, headers=headers, timeout=30)
            else:
                resp = requests.request(method, upstream_url, data=raw_body, params=params, headers=headers, timeout=30)
    except requests.RequestException as e:
        conn.close()
        raise HTTPException(status_code=502, detail=f"Upstream error: {e}")

    # Hash response JSON when possible
    try:
        resp_json = resp.json()
    except Exception:
        resp_json = {"non_json": True, "status_code": resp.status_code}
    resp_hash = canonical_keccak_hex(resp_json)

    # Emit on-chain proof and record
    call_id = str(uuid.uuid4())
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
            svc["service_id"],
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
    subgraph_base = os.getenv("SUBGRAPH_EXPLORER_BASE", "https://thegraph.com/explorer?query=0pi&search=")
    headers_out = {
        "X-0pi-Call-Id": call_id,
        "X-0pi-Tx-Hash": tx_hash,
        "X-0pi-Subgraph": f"{subgraph_base}{svc['service_id']}",
    }
    return Response(content=resp.content, status_code=resp.status_code, headers=headers_out, media_type=resp.headers.get("content-type", "application/octet-stream"))


@router.post("/{provider_name}")
async def paid_proxy(request: Request, provider_name: str = Path(...)) -> Response:
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
    try:
        logger.info(f"x402: provider={provider_name} service_id={service_id} listing_path={listing_path}")
    except Exception:
        pass
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

