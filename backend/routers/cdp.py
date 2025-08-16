from __future__ import annotations

import os
import logging
from typing import List, Dict

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cdp", tags=["cdp"])

from cdp import *
# # Import CDP SDK symbols at module level as requested
# try:
#     from cdp import *  # type: ignore  # noqa: F401,F403
#     CDP_IMPORT_ERROR: Exception | None = None
# except Exception as _e:  # pragma: no cover
#     CDP_IMPORT_ERROR = _e


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
    """Require NAME/PRIVATE_KEY credentials for Cdp.configure flow."""
    name = os.getenv("CDP_API_KEY_NAME")
    pkey = os.getenv("CDP_API_KEY_PRIVATE_KEY")
    if not (name and pkey):
        msg = "Missing CDP credentials. Set CDP_API_KEY_NAME and CDP_API_KEY_PRIVATE_KEY."
        logger.error(msg)
        raise HTTPException(status_code=500, detail=msg)






@router.get("/wallets", response_class=JSONResponse)
async def list_wallets() -> List[Dict[str, str]]:
    # Validate required env early to give actionable feedback
    _require_env()

    try:
        # Configure explicitly each request to avoid relying on startup import variance
        Cdp.configure(os.environ["CDP_API_KEY_NAME"], os.environ["CDP_API_KEY_PRIVATE_KEY"])  # type: ignore[name-defined]

        items: List[Dict[str, str]] = []
        net = _sdk_network(_network()) or "base-sepolia"
        list_fn = getattr(Wallet, "list", None)  # type: ignore[name-defined]
        if callable(list_fn):
            wallets = list_fn()  # type: ignore[call-arg]
            for w in wallets:
                addr = None
                da = getattr(w, "default_address", None)
                if da is not None:
                    addr = getattr(da, "address_id", None) or getattr(da, "address", None)
                items.append({
                    "account_id": getattr(w, "id", None) or getattr(w, "wallet_id", None),
                    "address": addr,
                    "network": net,
                })
        else:
            logger.info("Wallet.list() not available in this SDK version; returning empty list")
        return items
    except Exception as e:
        logger.exception("CDP list wallets failed (Cdp.configure flow)")
        raise HTTPException(status_code=500, detail=f"CDP list failed: {e.__class__.__name__}: {e}")

@router.post("/wallets", response_class=JSONResponse)
async def create_wallet() -> Dict[str, str]:
    # Validate required env early to give actionable feedback
    _require_env()

    try:
        Cdp.configure(os.environ["CDP_API_KEY_NAME"], os.environ["CDP_API_KEY_PRIVATE_KEY"])  # type: ignore[name-defined]

        kwargs = {}
        net = _sdk_network(_network())
        if net:
            kwargs["network_id"] = net
        wallet = Wallet.create(**kwargs)  # type: ignore[arg-type,name-defined]
        da = getattr(wallet, "default_address", None)
        addr = None
        if da is not None:
            addr = getattr(da, "address_id", None) or getattr(da, "address", None)
        return {"account_id": getattr(wallet, "id", None) or getattr(wallet, "wallet_id", None), "address": addr}
    except Exception as e:
        logger.exception("CDP create wallet failed (Cdp.configure flow)")
        raise HTTPException(status_code=500, detail=f"CDP create failed: {e.__class__.__name__}: {e}")

