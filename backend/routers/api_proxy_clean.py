from __future__ import annotations

import json as pyjson
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, HTTPException, Request, Response
from starlette.responses import StreamingResponse
import logging

from backend.db import get_connection

router = APIRouter(prefix="/api", tags=["proxy"]) 
open_router = APIRouter(tags=["proxy"])  # root-level catch-all

SAFE_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}

logger = logging.getLogger(__name__)

def _load_listing_by_provider(provider_name: str) -> dict | None:
    # Read MCP listing files and match name; prefer one that has a saved secret, else one with upstream_base_url
    from pathlib import Path
    base = Path(__file__).resolve().parents[1] / "mcp_listings"
    if not base.exists():
        return None
    matches: list[dict] = []
    for p in base.glob("*.json"):
        try:
            data = pyjson.loads(p.read_text())
            if data.get("name") == provider_name:
                data["_listing_path"] = str(p)
                matches.append(data)
        except Exception:
            continue
    if not matches:
        return None

    # Helper: does a secret exist for service_id?
    def has_secret(svc_id: str) -> bool:
        try:
            conn = get_connection(); cur = conn.cursor()
            cur.execute("SELECT 1 FROM service_secrets WHERE service_id=? LIMIT 1", (svc_id,))
            row = cur.fetchone()
            cur.close(); conn.close()
            return row is not None
        except Exception:
            return False

    # 1) Prefer listing with a secret
    for m in matches:
        sid = str(m.get("id") or "")
        if sid and has_secret(sid):
            m["_selection"] = "has_secret"
            return m
    # 2) Else prefer listing with upstream_base_url
    for m in matches:
        if m.get("upstream_base_url"):
            m["_selection"] = "has_upstream_base_url"
            return m
    # 3) Else return first
    matches[0]["_selection"] = "first_match"
    return matches[0]


def _find_secret_for(service_id: str) -> dict | None:
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute(
            "SELECT auth_location, auth_key, auth_template, api_key_cipher FROM service_secrets WHERE service_id=? ORDER BY rowid DESC LIMIT 1",
            (service_id,),
        )
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


def _inject_auth(headers: dict[str, str], params: dict[str, str], listing: dict) -> tuple[dict[str, str], dict[str, str]]:
    service_id = listing.get("id") or ""
    if not service_id:
        return headers, params
    secret = _find_secret_for(service_id)
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


def _redact(h: dict[str, str]) -> dict[str, str]:
    redacted = {}
    for k, v in h.items():
        lk = k.lower()
        if lk in {"authorization", "x-api-key", "api-key"}:
            redacted[k] = "***REDACTED***"
        else:
            redacted[k] = v
    return redacted


@router.api_route("/{provider_name}/{full_path:path}", methods=list(SAFE_METHODS))
@open_router.api_route("/{provider_name}/{full_path:path}", methods=list(SAFE_METHODS))
async def carry_through(request: Request, provider_name: str, full_path: str = "") -> Response:
    # Stage: received request
    try:
        logger.info(f"proxy: received {request.method} /{provider_name}/{full_path}")
    except Exception:
        pass

    # Load listing for provider
    listing = _load_listing_by_provider(provider_name)
    if not listing:
        logger.info(f"proxy: provider '{provider_name}' not found")
        raise HTTPException(status_code=404, detail=f"service '{provider_name}' not found")

    # Determine upstream
    tgt = request.headers.get("x-target-url") or request.query_params.get("target")
    if tgt:
        parsed = urlparse(tgt)
        if not parsed.scheme or not parsed.netloc:
            raise HTTPException(status_code=400, detail="invalid X-Target-Url/target")
        base_url = tgt
    else:
        upstream = (listing.get("upstream_base_url") or "").rstrip("/")
        if upstream:
            source = "upstream_base_url"
        else:
            docs = listing.get("docs_url") or ""
            parsed = urlparse(docs)
            if not parsed.scheme or not parsed.netloc:
                raise HTTPException(status_code=400, detail="service has no upstream_base_url and no resolvable docs host; provide X-Target-Url")
            upstream = f"{parsed.scheme}://{parsed.netloc}"
            source = "docs_url_host"
        path = ("/" + full_path.lstrip("/")) if full_path else "/"
        base_url = upstream + path

    try:
        logger.info(f"proxy: directing to upstream: {base_url} (source={source if 'source' in locals() else 'target_header'})")
    except Exception:
        pass

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

    # Ensure upstream returns readable body for logging and sane defaults for API calls
    outbound_headers.setdefault("Accept-Encoding", "identity")
    outbound_headers.setdefault("Accept", "application/json")
    outbound_headers.setdefault("User-Agent", "0pi-proxy/1.0 (+https://0pi.dev)")

    # Inject provider API key if saved
    outbound_headers, params = _inject_auth(outbound_headers, params, listing)

    # Auth logging: whether we injected credentials
    try:
        if any(k.lower() == "authorization" for k in outbound_headers.keys()):
            logger.info("proxy: auth injected via %s", listing.get("_selection", "unknown"))
        else:
            logger.info("proxy: no auth injected (no secret found)")
    except Exception:
        pass

    # Quick hint if no auth header present
    if all(h.lower() != "authorization" for h in outbound_headers.keys()):
        try:
            logger.info("proxy: hint - no Authorization header present; upstream may return 401/403")
        except Exception:
            pass

    try:
        logger.info(
            "proxy: forwarding %s %s ua=%s headers=%s params=%s",
            method,
            base_url,
            outbound_headers.get("User-Agent", ""),
            _redact(outbound_headers),
            params,
        )
    except Exception:
        pass

    # Body
    body = await request.body()

    try:
        with requests.Session() as s:
            r = s.request(method, base_url, headers=outbound_headers, params=params, data=body, timeout=30, stream=True)
    except requests.RequestException as e:
        logger.info(f"proxy: upstream error: {e}")
        raise HTTPException(status_code=502, detail=f"upstream error: {e}")

    try:
        logger.info(f"proxy: upstream responded status={r.status_code}")
    except Exception:
        pass

    # Peek first chunk for debug preview on 4xx/5xx
    iter_gen = r.iter_content(chunk_size=65536)
    first_chunk = b""
    try:
        first_chunk = next(iter_gen)
    except StopIteration:
        first_chunk = b""

    if r.status_code >= 400:
        ct = (r.headers.get("Content-Type") or "").lower()
        ce = (r.headers.get("Content-Encoding") or "").lower()
        preview = ""
        snippet = first_chunk[:4096] if first_chunk else b""
        # Best-effort decompress single-chunk gzip if server ignored Accept-Encoding
        if ce == "gzip":
            try:
                import gzip
                snippet = gzip.decompress(snippet)
            except Exception:
                pass
        try:
            if "application/json" in ct or ct.startswith("text/"):
                preview = snippet.decode("utf-8", errors="replace")
            else:
                preview = snippet[:128].hex()
        except Exception:
            preview = "<unprintable>"
        try:
            logger.info(f"proxy: upstream body snippet (truncated): {preview}")
        except Exception:
            pass

    # Stream response
    def gen_with_prefix():
        if first_chunk:
            yield first_chunk
        for chunk in iter_gen:
            if chunk:
                yield chunk

    resp_headers = {k: v for k, v in r.headers.items() if k.lower() not in {"content-encoding", "transfer-encoding", "connection"}}
    try:
        logger.info("proxy: responding to client")
    except Exception:
        pass
    return StreamingResponse(gen_with_prefix(), status_code=r.status_code, headers=resp_headers)

