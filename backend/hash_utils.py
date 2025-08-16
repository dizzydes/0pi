from __future__ import annotations

import json
from typing import Any

from eth_hash.auto import keccak

# Prefer the json_canonicalize implementation (RFC 8785). Fallback to sorted dumps.
try:
    from json_canonicalize import canonicalize as canonicalize_json  # type: ignore
except Exception:
    canonicalize_json = None  # type: ignore


def canonical_json_bytes(obj: Any) -> bytes:
    """Return RFC 8785 canonical JSON utf-8 bytes for an object, if available.

    Falls back to JSON with sorted keys and no whitespace if the canonicalizer
    isn't installed. This is deterministic but not fully RFC 8785 compliant.
    """
    if callable(canonicalize_json):  # type: ignore
        return canonicalize_json(obj).encode("utf-8")  # already canonical string
    # Fallback deterministic encoding
    return json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=False).encode("utf-8")


def keccak256_hex(data: bytes) -> str:
    return "0x" + keccak(data).hex()


def canonical_keccak_hex(obj: Any) -> str:
    return keccak256_hex(canonical_json_bytes(obj))

