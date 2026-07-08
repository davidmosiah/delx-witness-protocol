"""Helpers for Coinbase CDP request authentication."""

from __future__ import annotations

import base64
import secrets
import time
from urllib.parse import urlparse

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


def _load_private_key(api_key_secret: str):
    secret = (api_key_secret or "").strip()
    if not secret:
        raise ValueError("missing Coinbase API key secret")

    if "BEGIN" in secret:
        return serialization.load_pem_private_key(secret.encode("utf-8"), password=None), "ES256"

    raw = base64.b64decode(secret)
    if len(raw) == 64:
        raw = raw[:32]
    if len(raw) != 32:
        raise ValueError("unsupported Coinbase API key secret format")
    return ed25519.Ed25519PrivateKey.from_private_bytes(raw), "EdDSA"


def build_cdp_jwt(
    *,
    api_key_id: str,
    api_key_secret: str,
    request_method: str,
    request_host: str,
    request_path: str,
    expires_in: int = 120,
) -> str:
    private_key, algorithm = _load_private_key(api_key_secret)
    now = int(time.time())
    payload = {
        "sub": api_key_id,
        "iss": "cdp",
        "nbf": now,
        "exp": now + expires_in,
        "uris": [f"{request_method.upper()} {request_host}{request_path}"],
    }
    headers = {
        "alg": algorithm,
        "kid": api_key_id,
        "typ": "JWT",
        "nonce": secrets.token_hex(16),
    }
    return jwt.encode(payload, private_key, algorithm=algorithm, headers=headers)


def build_coinbase_auth_headers(
    *,
    api_key_id: str,
    api_key_secret: str,
    request_method: str,
    request_host: str,
    request_path: str,
) -> dict[str, str]:
    token = build_cdp_jwt(
        api_key_id=api_key_id,
        api_key_secret=api_key_secret,
        request_method=request_method,
        request_host=request_host,
        request_path=request_path,
    )
    return {
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
    }


def build_coinbase_auth_headers_for_url(
    *,
    api_key_id: str,
    api_key_secret: str,
    request_method: str,
    request_url: str,
) -> dict[str, str]:
    parsed = urlparse(request_url)
    return build_coinbase_auth_headers(
        api_key_id=api_key_id,
        api_key_secret=api_key_secret,
        request_method=request_method,
        request_host=parsed.netloc,
        request_path=parsed.path or "/",
    )
