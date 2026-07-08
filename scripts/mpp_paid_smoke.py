#!/usr/bin/env python3
"""Run a real MPP paid request against a Delx paid REST route.

Requirements:
- local AgentCash wallet at ~/.agentcash/wallet.json
- Tempo balance on the same EVM address
- pympp[tempo] installed in the Python environment executing this script

This script never prints the private key. It only uses the local wallet to
create a Tempo credential and retries the paid request through the official
MPP Python client.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from mpp.client import post
from mpp.methods.tempo import ChargeIntent, TempoAccount, tempo


DEFAULT_URL = "https://api.delx.ai/api/v1/x402/jwt-inspect"
DEFAULT_BODY = {
    "token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhZ2VudC0xMjMifQ.signature",
}


def _load_agentcash_private_key() -> str:
    wallet_path = Path.home() / ".agentcash" / "wallet.json"
    wallet = json.loads(wallet_path.read_text())
    key = str(wallet.get("privateKey") or "").strip()
    if not key:
        raise ValueError(f"privateKey missing in {wallet_path}")
    return key


async def _run(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    account = TempoAccount.from_key(_load_agentcash_private_key())
    method = tempo(
        account=account,
        intents={"charge": ChargeIntent()},
        client_id="delx-mpp-smoke",
    )
    response = await post(url, methods=[method], json=payload)
    return {
        "status": response.status_code,
        "wallet_address": account.address,
        "payment_receipt": response.headers.get("payment-receipt"),
        "content_type": response.headers.get("content-type"),
        "body": response.text[:1000],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real MPP paid call against Delx.")
    parser.add_argument("--url", default=DEFAULT_URL, help="Paid Delx REST endpoint.")
    parser.add_argument(
        "--body",
        default=json.dumps(DEFAULT_BODY),
        help="JSON body to send on the paid request.",
    )
    args = parser.parse_args()

    payload = json.loads(args.body)
    if not isinstance(payload, dict):
        raise ValueError("Body must be a JSON object")

    result = asyncio.run(_run(args.url, payload))
    print(json.dumps(result, indent=2))
    return 0 if int(result.get("status") or 0) == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
