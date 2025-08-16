from __future__ import annotations

import base64
import os
import secrets
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _get_key() -> bytes:
    b64 = os.getenv("ENCRYPTION_KEY")
    if not b64:
        raise RuntimeError("ENCRYPTION_KEY is not set in environment/.env")
    try:
        key = base64.b64decode(b64, validate=True)
    except Exception as e:
        raise RuntimeError("ENCRYPTION_KEY must be valid base64") from e
    if len(key) not in (16, 24, 32):
        # Support AES-128/192/256, recommend 32 bytes
        raise RuntimeError("ENCRYPTION_KEY must decode to 16/24/32 bytes; recommend 32 bytes")
    return key


def encrypt_secret(plaintext: bytes, aad: Optional[bytes] = None) -> bytes:
    key = _get_key()
    aesgcm = AESGCM(key)
    nonce = secrets.token_bytes(12)  # 96-bit nonce for GCM
    ct = aesgcm.encrypt(nonce, plaintext, aad)
    return nonce + ct  # store nonce||ciphertext+tag


def decrypt_secret(blob: bytes, aad: Optional[bytes] = None) -> bytes:
    key = _get_key()
    aesgcm = AESGCM(key)
    if len(blob) < 12:
        raise ValueError("ciphertext is too short")
    nonce, ct = blob[:12], blob[12:]
    return aesgcm.decrypt(nonce, ct, aad)

