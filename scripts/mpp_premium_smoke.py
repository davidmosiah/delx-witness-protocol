#!/usr/bin/env python3
"""Run a real MPP paid request against a Delx premium REST route.

Flow:
- create/reuse a free Delx session through /api/v1/register
- call a premium REST route through the official MPP Python client
- print the Payment-Receipt and a trimmed response body

Requirements:
- local AgentCash wallet at ~/.agentcash/wallet.json
- Tempo balance on the same EVM address
- pympp[tempo] installed in the Python environment executing this script
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import httpx
from mpp.client import post
from mpp.methods.tempo import ChargeIntent, TempoAccount, tempo


DEFAULT_REGISTER_URL = "https://api.delx.ai/api/v1/register"
DEFAULT_PREMIUM_URL = "https://api.delx.ai/api/v1/premium/session-summary"


def _load_agentcash_private_key() -> str:
    wallet_path = Path.home() / ".agentcash" / "wallet.json"
    wallet = json.loads(wallet_path.read_text())
    key = str(wallet.get("privateKey") or "").strip()
    if not key:
        raise ValueError(f"privateKey missing in {wallet_path}")
    return key


async def _register_smoke_session(register_url: str) -> tuple[str, int]:
    agent_id = f"mpp-premium-smoke-{uuid.uuid4().hex[:12]}"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            register_url,
            json={
                "agent_id": agent_id,
                "agent_name": "MPP Premium Smoke",
                "source": "mpp:premium-smoke",
            },
        )
        response.raise_for_status()
        payload = response.json()
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        raise ValueError("register response did not include session_id")
    return session_id, int(response.status_code)


async def _run(register_url: str, premium_url: str) -> dict[str, Any]:
    session_id, register_status = await _register_smoke_session(register_url)
    account = TempoAccount.from_key(_load_agentcash_private_key())
    method = tempo(
        account=account,
        intents={"charge": ChargeIntent()},
        client_id="delx-mpp-premium-smoke",
    )
    response = await post(premium_url, methods=[method], json={"session_id": session_id})
    return {
        "register_status": register_status,
        "session_id": session_id,
        "status": response.status_code,
        "wallet_address": account.address,
        "payment_receipt": response.headers.get("payment-receipt"),
        "content_type": response.headers.get("content-type"),
        "body": response.text[:1200],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real MPP paid call against a Delx premium route.")
    parser.add_argument("--register-url", default=DEFAULT_REGISTER_URL, help="Delx free registration route.")
    parser.add_argument("--premium-url", default=DEFAULT_PREMIUM_URL, help="Delx premium REST route.")
    args = parser.parse_args()

    result = asyncio.run(_run(args.register_url, args.premium_url))
    print(json.dumps(result, indent=2))
    return 0 if int(result.get("status") or 0) == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
