"""Explicit application context for Delx runtime globals.

Tests and production still patch `server.store` / `server.engine`.
`get_app_context()` always reads those live module attributes so monkeypatches
keep working. New code should prefer this helper over scattering imports.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AppContext:
    """Runtime handles shared across MCP, REST, and A2A surfaces."""

    store: Any
    engine: Any | None = None
    http_client: Any | None = None
    payment_http_client: Any | None = None


def get_app_context() -> AppContext:
    """Return a snapshot bound to the current `server` module globals."""
    import server as server_mod

    return AppContext(
        store=server_mod.store,
        engine=getattr(server_mod, "engine", None),
        http_client=getattr(server_mod, "http_client", None),
        payment_http_client=getattr(server_mod, "payment_http_client", None),
    )


def bind_app_context(
    *,
    store: Any | None = None,
    engine: Any | None = None,
    http_client: Any | None = None,
    payment_http_client: Any | None = None,
) -> AppContext:
    """Write selected fields back onto `server` (the patchable surface)."""
    import server as server_mod

    if store is not None:
        server_mod.store = store
    if engine is not None:
        server_mod.engine = engine
    if http_client is not None:
        server_mod.http_client = http_client
    if payment_http_client is not None:
        server_mod.payment_http_client = payment_http_client
    return get_app_context()
