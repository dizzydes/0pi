from __future__ import annotations

import os
import logging
import time
from typing import List, Dict, Optional

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from backend.cdp_auth import generate_bearer_jwt

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cdp", tags=["cdp"])



def _network() -> str:
    return os.getenv("CDP_NETWORK", os.getenv("X402_NETWORK", os.getenv("X402_CHAIN", "base")))


def _sdk_network(net: str | None) -> str | None:
    # Map simple env values to SDK network ids when possible
    if not net:
        return None
    n = net.strip().lower()
    if n in ("base", "base-mainnet", "mainnet"):
        return "base-mainnet"
    if n in ("base-sepolia", "sepolia", "testnet"):
        return "base-sepolia"
    return net


def _require_env() -> None:
    # REST mode: credentials supplied via JWTs (Authorization, X-Wallet-Auth). Nothing to validate here.
    return None

# In-memory token cache for externally supplied tokens (headers/env)
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


def _now() -> float:
    return time.time()


def _read_cached(name: str) -> Optional[str]:
    item = _TOKEN_CACHE.get(name)
    if not item:
        return None
    token, exp = item
    if _now() < exp:
        return token
    _TOKEN_CACHE.pop(name, None)
    return None


def _write_cache(name: str, token: str, ttl_seconds: int) -> None:
    _TOKEN_CACHE[name] = (token, _now() + max(1, ttl_seconds))

def _bearer_generate(method: str, host: str, path: str) -> str:
    # Use in-process EdDSA signer with CDP_KEY_ID/SECRET
    return generate_bearer_jwt(method, host, path)


def _bearer_from(request: Request, method: str, host: str, path: str) -> str:
    # 1) Incoming header
    hdr = request.headers.get("authorization") or request.headers.get("Authorization")
    if hdr and hdr.strip().lower().startswith("bearer "):
        return hdr.split(" ", 1)[1].strip()
    # 2) Env pre-supplied
    env = os.getenv("BEARER_JWT")
    if env:
        return env
    # 3) Generate per request
    return _bearer_generate(method, host, path)

def _wallet_auth_from(request: Request) -> Optional[str]:
    # Only use if caller supplies it or environment has it; we are not generating wallet JWTs by default
    hdr = request.headers.get("x-wallet-auth") or request.headers.get("X-Wallet-Auth")
    if hdr:
        return hdr
    env = os.getenv("WALLET_AUTH_JWT")
    if env:
        return env
    return None






@router.get("/wallets", response_class=JSONResponse)
async def list_wallets(request: Request) -> List[Dict[str, str]]:
    host = os.getenv("CDP_API_HOST", "api.cdp.coinbase.com")
    path = os.getenv("CDP_LIST_ACCOUNTS_PATH", "/platform/v2/evm/accounts")
    url = f"https://{host}{path}"

    # Generate or fetch Bearer tied to this request target
    bearer = _bearer_from(request, "GET", host, path)

    try:
        with requests.Session() as s:
            s.headers.update({
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "0pi-backend/requests",
                "X-Debug-Target": f"{host}{path}",
            })
            r = s.get(url, headers={"Authorization": f"Bearer {bearer}"}, timeout=15)
            # Retry once on unauthorized by regenerating (unconstrained)
            if r.status_code in (401, 403):
                bearer = generate_bearer_jwt("GET", host, path, constrain=False)
                r = s.get(url, headers={"Authorization": f"Bearer {bearer}"}, timeout=15)
        if r.status_code >= 400:
            logger.error("CDP list wallets error %s: %s", r.status_code, r.text)
            raise HTTPException(status_code=r.status_code, detail={"upstream": "cdp", "status": r.status_code, "body": r.text})
        data = r.json()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("CDP list wallets request failed")
        raise HTTPException(status_code=502, detail=f"CDP list wallets failed: {e.__class__.__name__}: {e}")

    net = _sdk_network(_network()) or "base-sepolia"
    items: List[Dict[str, str]] = []
    rows = data.get("data") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        rows = []
    for w in rows:
        addr = None
        wid = None
        if isinstance(w, dict):
            da = w.get("default_address")
            if isinstance(da, dict):
                addr = da.get("address_id") or da.get("address")
            addr = addr or w.get("address")
            wid = w.get("id") or w.get("wallet_id") or w.get("account_id")
        items.append({
            "account_id": str(wid or ""),
            "address": str(addr or ""),
            "network": net,
        })
    return items
@router.post("/wallets", response_class=JSONResponse)
async def create_wallet(request: Request) -> Dict[str, str]:
    host = os.getenv("CDP_API_HOST", "api.cdp.coinbase.com")
    path = os.getenv("CDP_CREATE_ACCOUNT_PATH", "/platform/v2/evm/accounts")
    url = f"https://{host}{path}"

    body = {"network_id": _sdk_network(_network()) or "base-mainnet"}

    # Generate or fetch Bearer tied to this request target
    bearer = _bearer_from(request, "POST", host, path)
    wallet_auth = _wallet_auth_from(request)
    if not wallet_auth:
        raise HTTPException(status_code=401, detail="Missing Wallet Auth token. Provide X-Wallet-Auth header or set WALLET_AUTH_JWT env.")

    try:
        with requests.Session() as s:
            s.headers.update({
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "0pi-backend/requests",
                "X-Debug-Target": f"{host}{path}",
            })
            r = s.post(url, json=body, headers={
                "Authorization": f"Bearer {bearer}",
                "X-Wallet-Auth": wallet_auth,
            }, timeout=20)
            if r.status_code in (401, 403):
                bearer = generate_bearer_jwt("POST", host, path, constrain=False)
                r = s.post(url, json=body, headers={
                    "Authorization": f"Bearer {bearer}",
                    "X-Wallet-Auth": wallet_auth,
                }, timeout=20)
        if r.status_code >= 400:
            logger.error("CDP create wallet error %s: %s", r.status_code, r.text)
            raise HTTPException(status_code=r.status_code, detail={"upstream": "cdp", "status": r.status_code, "body": r.text})
        data = r.json()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("CDP create wallet request failed")
        raise HTTPException(status_code=502, detail=f"CDP create wallet failed: {e.__class__.__name__}: {e}")

    addr = None
    wid = None
    if isinstance(data, dict):
        da = data.get("default_address") if isinstance(data.get("default_address"), dict) else None
        if da:
            addr = da.get("address_id") or da.get("address")
        addr = addr or data.get("address")
        wid = data.get("id") or data.get("wallet_id") or data.get("account_id")
    # Cache tokens if they were generated on-demand
    if not (request.headers.get("authorization") or request.headers.get("Authorization") or os.getenv("BEARER_JWT")):
        if bearer:
            _write_cache("bearer", bearer, int(os.getenv("CDP_BEARER_JWT_TTL", "110")))
    if not (request.headers.get("x-wallet-auth") or request.headers.get("X-Wallet-Auth") or os.getenv("WALLET_AUTH_JWT")):
        if wallet_auth:
            _write_cache("wallet", wallet_auth, int(os.getenv("CDP_WALLET_JWT_TTL", "50")))

    return {"account_id": str(wid or ""), "address": str(addr or ""), "network": _sdk_network(_network()) or "base-mainnet"}
