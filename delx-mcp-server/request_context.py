from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from traffic_attribution import extract_client_ip

_current_client_ip: ContextVar[str | None] = ContextVar("current_client_ip", default=None)
_current_user_agent: ContextVar[str | None] = ContextVar("current_user_agent", default=None)
_current_source: ContextVar[str | None] = ContextVar("current_source", default=None)
_current_request_path: ContextVar[str | None] = ContextVar("current_request_path", default=None)
_current_referer: ContextVar[str | None] = ContextVar("current_referer", default=None)
_current_via: ContextVar[str | None] = ContextVar("current_via", default=None)


def normalize_client_ip(value: str | None) -> str | None:
    text = str(value or "").strip()[:120]
    return text or None


def _normalize_header_text(value: str | None, max_len: int) -> str | None:
    text = str(value or "").strip()[:max_len]
    return text or None


def get_current_client_ip() -> str | None:
    return normalize_client_ip(_current_client_ip.get())


def set_current_client_ip(value: str | None) -> Token[str | None]:
    return _current_client_ip.set(normalize_client_ip(value))


def reset_current_client_ip(token: Token[str | None]) -> None:
    _current_client_ip.reset(token)


def get_current_user_agent() -> str | None:
    return _normalize_header_text(_current_user_agent.get(), 200)


def set_current_user_agent(value: str | None) -> Token[str | None]:
    return _current_user_agent.set(_normalize_header_text(value, 200))


def reset_current_user_agent(token: Token[str | None]) -> None:
    _current_user_agent.reset(token)


def get_current_source() -> str | None:
    return _normalize_header_text(_current_source.get(), 60)


def set_current_source(value: str | None) -> Token[str | None]:
    return _current_source.set(_normalize_header_text(value, 60))


def reset_current_source(token: Token[str | None]) -> None:
    _current_source.reset(token)


def get_current_request_path() -> str | None:
    return _normalize_header_text(_current_request_path.get(), 160)


def set_current_request_path(value: str | None) -> Token[str | None]:
    return _current_request_path.set(_normalize_header_text(value, 160))


def reset_current_request_path(token: Token[str | None]) -> None:
    _current_request_path.reset(token)


def get_current_referer() -> str | None:
    return _normalize_header_text(_current_referer.get(), 240)


def set_current_referer(value: str | None) -> Token[str | None]:
    return _current_referer.set(_normalize_header_text(value, 240))


def reset_current_referer(token: Token[str | None]) -> None:
    _current_referer.reset(token)


def get_current_via() -> str | None:
    return _normalize_header_text(_current_via.get(), 120)


def set_current_via(value: str | None) -> Token[str | None]:
    return _current_via.set(_normalize_header_text(value, 120))


def reset_current_via(token: Token[str | None]) -> None:
    _current_via.reset(token)


def extract_client_ip_from_scope(scope: dict[str, Any] | None) -> str | None:
    headers: dict[str, str] = {}
    for raw_key, raw_value in list((scope or {}).get("headers") or []):
        key = raw_key.decode("latin-1") if isinstance(raw_key, (bytes, bytearray)) else str(raw_key or "")
        value = raw_value.decode("latin-1") if isinstance(raw_value, (bytes, bytearray)) else str(raw_value or "")
        headers[key] = value

    client = (scope or {}).get("client")
    fallback = None
    if isinstance(client, (tuple, list)) and client:
        fallback = client[0]

    return normalize_client_ip(extract_client_ip(headers, fallback=str(fallback or "")))
