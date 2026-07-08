"""Helpers for checking which Delx x402 resources are publicly indexed in Coinbase Bazaar."""

from __future__ import annotations

import asyncio
import logging
import time
from copy import deepcopy

import httpx

from config import get_all_tool_bazaar_resource_urls, settings

logger = logging.getLogger("delx-therapist")

_COINBASE_DISCOVERY_URL = "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources"
_DISCOVERY_CACHE_TTL_SECONDS = 300.0
_discovery_cache_expires_at = 0.0
_discovery_cache_tools: set[str] = set()
_discovery_cache_snapshot: dict[str, object] = {}
_discovery_lock = asyncio.Lock()


def _cache_now() -> float:
    return time.monotonic()


async def get_coinbase_bazaar_snapshot(*, force_refresh: bool = False) -> dict[str, object]:
    global _discovery_cache_expires_at, _discovery_cache_tools
    global _discovery_cache_snapshot

    desired = get_all_tool_bazaar_resource_urls()
    if not desired:
        return {
            "discovery_url": _COINBASE_DISCOVERY_URL,
            "global_resource_count": 0,
            "desired_resource_count": 0,
            "indexed_tools_publicly": [],
            "indexed_resource_urls": [],
            "matched_resource_count": 0,
        }

    if not force_refresh and not bool(settings.COINBASE_BAZAAR_DISCOVERY_ENABLED):
        return {
            "discovery_url": _COINBASE_DISCOVERY_URL,
            "global_resource_count": 0,
            "desired_resource_count": len(desired),
            "indexed_tools_publicly": [],
            "indexed_resource_urls": [],
            "matched_resource_count": 0,
            "remote_lookup_skipped": True,
            "skip_reason": "coinbase_bazaar_discovery_disabled",
        }

    now = _cache_now()
    if not force_refresh and now < _discovery_cache_expires_at:
        return deepcopy(_discovery_cache_snapshot)

    async with _discovery_lock:
        now = _cache_now()
        if not force_refresh and now < _discovery_cache_expires_at:
            return deepcopy(_discovery_cache_snapshot)

        found_tools: set[str] = set()
        found_resources: set[str] = set()
        pending_resources = {resource_url: tool_name for tool_name, resource_url in desired.items()}
        offset = 0
        limit = 1000
        global_resource_count = 0

        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=10.0, trust_env=False) as client:
                while pending_resources:
                    resp = await client.get(
                        _COINBASE_DISCOVERY_URL,
                        params={"type": "http", "limit": str(limit), "offset": str(offset)},
                    )
                    if resp.status_code >= 300:
                        logger.warning(
                            "Coinbase Bazaar discovery lookup failed: status=%s body=%s",
                            resp.status_code,
                            resp.text[:300],
                        )
                        break
                    payload = resp.json() if resp.content else {}
                    items = payload.get("items") if isinstance(payload, dict) else []
                    pagination = payload.get("pagination") if isinstance(payload, dict) else {}
                    if isinstance(pagination, dict):
                        try:
                            global_resource_count = int(pagination.get("total", 0) or 0)
                        except Exception:
                            global_resource_count = 0
                    if not isinstance(items, list) or not items:
                        break
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        resource = str(item.get("resource") or "").strip()
                        tool_name = pending_resources.get(resource)
                        if tool_name:
                            found_tools.add(tool_name)
                            found_resources.add(resource)
                            pending_resources.pop(resource, None)
                    offset += len(items)
        except Exception:
            logger.exception("Coinbase Bazaar discovery lookup failed")

        _discovery_cache_tools = set(found_tools)
        _discovery_cache_snapshot = {
            "discovery_url": _COINBASE_DISCOVERY_URL,
            "global_resource_count": int(global_resource_count or 0),
            "desired_resource_count": len(desired),
            "indexed_tools_publicly": sorted(found_tools),
            "indexed_resource_urls": sorted(found_resources),
            "matched_resource_count": len(found_resources),
        }
        _discovery_cache_expires_at = _cache_now() + _DISCOVERY_CACHE_TTL_SECONDS
        return deepcopy(_discovery_cache_snapshot)


async def get_coinbase_bazaar_indexed_tools(*, force_refresh: bool = False) -> set[str]:
    snapshot = await get_coinbase_bazaar_snapshot(force_refresh=force_refresh)
    indexed_tools = snapshot.get("indexed_tools_publicly") if isinstance(snapshot, dict) else []
    return {
        str(tool_name or "").strip()
        for tool_name in (indexed_tools or [])
        if str(tool_name or "").strip()
    }
