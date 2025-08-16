from __future__ import annotations

import io
import json as pyjson
from urllib.parse import urlparse, urlunparse, urlencode

import requests
from fastapi import APIRouter, HTTPException, Request, Response
from starlette.responses import StreamingResponse

from backend.db import get_connection

router = APIRouter(prefix="/api", tags=["proxy"]) 

SAFE_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}


def _fetch_service(provider_name: str) -> dict | None:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT service_id, provider_id, api_docs_url FROM services s WHERE s.provider_id IS NOT NULL AND s.service_id IN (SELECT service_id FROM services)")
        # Direct lookup by provider_name via MCP listing file to avoid schema churn
        # We stored listing files keyed by service_id; read provider_name from JSON files if needed.
    finally:
        cur.close(); conn.close()
    return None


def _load_listing_by_provider(provider_name: str) -> dict | None:
    # Read MCP listing files and match name
    from pathlib import Path
    base = Path(__file__).resolve().parents[1] / "mcp_listings"
    if not base.exists():
        return None
    for p in base.glob("*.json"):
        try:
            data = pyjson.loads(p.read_text())
            if data.get("name") == provider_name:
                data["_listing_path"] = str(p)
                return data
        except Exception:
            continue
    return None


def _find_secret_for(service_id: str) -> dict | None:
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT auth_location, auth_key, auth_template, api_key_cipher FROM service_secrets WHERE service_id=? ORDER BY rowid DESC LIMIT 1", (service_id,))
        row = cur.fetchone()
        if not row:
            return None
        return {
            "auth_location": row[0],
            "auth_key": row[1],
            "auth_template": row[2],
            "api_key_cipher": row[3],
        }
    finally:
        cur.close(); conn.close()


def _decrypt(cipher: bytes, aad: str) -> str | None:
    try:
        from backend.crypto import decrypt_secret  # type: ignore
        return decrypt_secret(cipher, aad.encode("utf-8")).decode("utf-8")
    except Exception:
        return None


def _inject_auth(headers: dict[str, str], params: dict[str, str], secret: dict | None, service_id: str) -> tuple[dict[str, str], dict[str, str]]:
    if not secret:
        return headers, params
    key = None
    if secret.get("api_key_cipher"):
        key = _decrypt(secret["api_key_cipher"], service_id)
    if not key:
        return headers, params
    location = (secret.get("auth_location") or "header").lower()
    auth_key = secret.get("auth_key") or ("Authorization" if location == "header" else "api_key")
    template = secret.get("auth_template") or ("Bearer {key}" if location == "header" else "{key}")
    value = template.replace("{key}", key)
    if location == "header":
        headers[auth_key] = value
    else:
        params[auth_key] = value
    return headers, params


@router.api_route("/{provider_name}/{full_path:path}", methods=list(SAFE_METHODS))
async def carry_through(request: Request, provider_name: str, full_path: str = "") -> Response:
    # Load listing for provider
    listing = _load_listing_by_provider(provider_name)
    if not listing:
        raise HTTPException(status_code=404, detail=f"service '{provider_name}' not found")

    service_id = listing.get("id", provider_name)

    # Determine upstream
    # Priority: X-Target-Url header, then target query param, else build from docs_url host + incoming path
    tgt = request.headers.get("x-target-url") or request.query_params.get("target")
    if tgt:
        parsed = urlparse(tgt)
        if not parsed.scheme or not parsed.netloc:
            raise HTTPException(status_code=400, detail="invalid X-Target-Url/target")
        base_url = tgt
    else:
        docs = listing.get("docs_url") or ""
        parsed = urlparse(docs)
        if not parsed.scheme or not parsed.netloc:
            raise HTTPException(status_code=400, detail="service has no resolvable docs host; provide X-Target-Url")
        # carry through path
        base_url = urlunparse((parsed.scheme, parsed.netloc, "/" + full_path, "", "", ""))

    # Prepare outbound request
    method = request.method.upper()
    if method not in SAFE_METHODS:
        raise HTTPException(status_code=405, detail="method not allowed")

    # Copy headers except hop-by-hop
    outbound_headers: dict[str, str] = {}
    for k, v in request.headers.items():
        lk = k.lower()
        if lk in {"host", "content-length", "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"}:
            continue
        if lk in {"x-target-url"}:
            continue
        outbound_headers[k] = v

    # Query params carry-through
    params = dict(request.query_params)
    params.pop("target", None)

    # Inject provider API key if saved
    secret = _find_secret_for(service_id)
    outbound_headers, params = _inject_auth(outbound_headers, params, secret, service_id)

    # Body
    body = await request.body()

    try:
        with requests.Session() as s:
            r = s.request(method, base_url, headers=outbound_headers, params=params, data=body, timeout=30, stream=True)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"upstream error: {e}")

    # Build streaming response
    def gen():
        for chunk in r.iter_content(chunk_size=65536):
            if chunk:
                yield chunk
    resp_headers = {k: v for k, v in r.headers.items() if k.lower() not in {"content-encoding", "transfer-encoding", "connection"}}
    return StreamingResponse(gen(), status_code=r.status_code, headers=resp_headers)
