from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from typing import Optional, Tuple

from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import ed25519, ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

# Simple in-memory cache
_CACHE: dict[tuple[str, str, str, str], tuple[str, float]] = {}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _now() -> int:
    return int(time.time())


def _canonicalize_json(obj: object) -> str:
    # Deterministic JSON with sorted keys and no spaces
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))

def _load_private_key(secret: str):
    s = secret.strip()
    # Try PEM first
    if "BEGIN" in s:
        key = serialization.load_pem_private_key(s.encode("utf-8"), password=None)
        if isinstance(key, (ed25519.Ed25519PrivateKey, ec.EllipticCurvePrivateKey)):
            return key
        raise ValueError("Provided PEM key must be Ed25519 or EC (P-256) private key")
    # Try base64 DER (PKCS#8)
    try:
        der = base64.b64decode(s, validate=True)
        key = serialization.load_der_private_key(der, password=None)
        if isinstance(key, (ed25519.Ed25519PrivateKey, ec.EllipticCurvePrivateKey)):
            return key
        raise ValueError("Provided DER key must be Ed25519 or EC (P-256) private key")
    except Exception:
        # Try raw 32-byte Ed25519 seed (base64)
        raw = base64.b64decode(s)
        if len(raw) == 32:
            return ed25519.Ed25519PrivateKey.from_private_bytes(raw)
        raise ValueError("KEY_SECRET format not recognized; expected PEM, base64 DER PKCS#8, or base64 32-byte Ed25519 seed")


def _sign_ed25519(priv: ed25519.Ed25519PrivateKey, header: dict, payload: dict) -> str:
    header_b64 = _b64url(_canonicalize_json(header).encode("utf-8"))
    payload_b64 = _b64url(_canonicalize_json(payload).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = priv.sign(signing_input)
    sig_b64 = _b64url(sig)
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def _sign_es256(priv: ec.EllipticCurvePrivateKey, header: dict, payload: dict) -> str:
    # JOSE ES256 requires R||S (64 bytes) base64url, not DER
    header_b64 = _b64url(_canonicalize_json(header).encode("utf-8"))
    payload_b64 = _b64url(_canonicalize_json(payload).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    der_sig = priv.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der_sig)
    r_bytes = r.to_bytes(32, byteorder="big")
    s_bytes = s.to_bytes(32, byteorder="big")
    sig_b64 = _b64url(r_bytes + s_bytes)
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def _jwt_cache_key(key_id: str, method: str, host: str, path: str) -> tuple[str, str, str, str]:
    return (key_id, method.upper(), host.lower(), path)


def generate_bearer_jwt(method: str, host: str, path: str, *, expires_in: int = 110, constrain: bool = True) -> str:
    """Generate a Bearer JWT for CDP server auth using EdDSA (Ed25519).

    Requires env:
      - CDP_KEY_ID (aka KEY_NAME)
      - CDP_KEY_SECRET (Ed25519 private key: PEM, base64 DER PKCS#8, or base64 32-byte seed)
    """
    key_id = os.getenv("CDP_KEY_ID") or os.getenv("KEY_NAME")
    key_secret = os.getenv("CDP_KEY_SECRET") or os.getenv("KEY_SECRET")
    if not key_id or not key_secret:
        raise RuntimeError("Missing CDP_KEY_ID or CDP_KEY_SECRET in environment")

    # Cache by (key_id, method, host, path)
    ck = _jwt_cache_key(key_id, method, host, path if constrain else "*")
    cached = _CACHE.get(ck)
    now = _now()
    if cached and cached[1] > now:
        return cached[0]

    priv = _load_private_key(key_secret)

    iat = now
    exp = now + max(30, min(300, int(expires_in)))  # 30..300s
    # Constrain token to a specific request target as per doc style
    payload = {
        "iat": iat,
        "nbf": iat,
        "exp": exp,
    }
    if constrain:
        payload["uris"] = [f"{method.upper()} {host}{path}"]
    else:
        # Fallback: include audience/host only
        payload["aud"] = host

    if isinstance(priv, ed25519.Ed25519PrivateKey):
        alg_hdr = os.getenv("CDP_JWT_ALG", "EdDSA")  # Allow override to "Ed25519" if server expects that
        header = {"alg": alg_hdr, "typ": "JWT", "kid": key_id}
        jwt = _sign_ed25519(priv, header, payload)
    elif isinstance(priv, ec.EllipticCurvePrivateKey):
        # Assume P-256 (ES256)
        if not isinstance(priv.curve, ec.SECP256R1):
            raise ValueError("Unsupported EC curve; expected P-256 for ES256")
        header = {"alg": "ES256", "typ": "JWT", "kid": key_id}
        jwt = _sign_es256(priv, header, payload)
    else:
        raise ValueError("Unsupported private key type")

    # Cache with small slack (expires_in - 5s)
    _CACHE[ck] = (jwt, exp - 5)
    return jwt


def generate_wallet_jwt(wallet_secret_pem_or_b64: str, method: str, host: str, path: str, body: Optional[object] = None, *, expires_in: int = 55) -> str:
    """Generate Wallet Auth JWT (X-Wallet-Auth) with EdDSA.

    The secret should be the Wallet Secret from CDP, as PEM or base64 DER PKCS#8.
    """
    priv = _load_private_key(wallet_secret_pem_or_b64)

    now = _now()
    iat = now
    exp = now + max(20, min(120, int(expires_in)))

    uri = f"{method.upper()} {host}{path}"
    payload: dict = {
        "iat": iat,
        "nbf": iat,
        "exp": exp,
        "jti": base64.urlsafe_b64encode(os.urandom(16)).decode("ascii").rstrip("="),
        "uris": [uri],
    }

    if body is not None:
        try:
            canon = _canonicalize_json(body)
            req_hash = hashlib.sha256(canon.encode("utf-8")).hexdigest()
            payload["reqHash"] = req_hash
        except Exception:
            # best-effort; skip if body can't be canonicalized
            pass

    if isinstance(priv, ed25519.Ed25519PrivateKey):
        alg_hdr = os.getenv("CDP_WALLET_JWT_ALG", os.getenv("CDP_JWT_ALG", "EdDSA"))
        header = {"alg": alg_hdr, "typ": "JWT"}
        return _sign_ed25519(priv, header, payload)
    elif isinstance(priv, ec.EllipticCurvePrivateKey):
        if not isinstance(priv.curve, ec.SECP256R1):
            raise ValueError("Unsupported EC curve for wallet JWT; expected P-256 for ES256")
        header = {"alg": "ES256", "typ": "JWT"}
        return _sign_es256(priv, header, payload)
    else:
        raise ValueError("Unsupported private key type for wallet JWT")
