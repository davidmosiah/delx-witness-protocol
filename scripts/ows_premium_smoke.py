#!/usr/bin/env python3
"""Run a real OWS paid request against a Delx premium x402 route.

Flow:
- ensure an OWS wallet exists locally
- import the local AgentCash EVM key into OWS if needed
- register a fresh free Delx session
- pay a premium Delx x402 route through the official OWS CLI

Requirements:
- local AgentCash wallet at ~/.agentcash/wallet.json
- Base USDC on the same EVM address
- Node.js available to run `npx @open-wallet-standard/core`
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any

import httpx


DEFAULT_REGISTER_URL = "https://api.delx.ai/api/v1/register"
DEFAULT_PREMIUM_URL = "https://api.delx.ai/api/v1/premium/session-summary"
DEFAULT_WALLET_NAME = "delx-ows-smoke"
OWS_CMD = ["npx", "-y", "@open-wallet-standard/core"]


def _load_agentcash_private_key() -> str:
    wallet_path = Path.home() / ".agentcash" / "wallet.json"
    wallet = json.loads(wallet_path.read_text())
    key = str(wallet.get("privateKey") or "").strip()
    if not key:
        raise ValueError(f"privateKey missing in {wallet_path}")
    return key


def _run_ows(args: list[str], *, env: dict[str, str] | None = None, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        OWS_CMD + args,
        check=check,
        text=True,
        capture_output=True,
        env=env,
        timeout=180,
    )


def _ensure_wallet(wallet_name: str) -> None:
    listed = _run_ows(["wallet", "list"])
    if wallet_name in listed.stdout:
        return
    env = os.environ.copy()
    env["OWS_PRIVATE_KEY"] = _load_agentcash_private_key()
    imported = _run_ows(
        ["wallet", "import", "--name", wallet_name, "--private-key", "--chain", "evm"],
        env=env,
    )
    if imported.returncode != 0:
        raise RuntimeError(imported.stderr.strip() or imported.stdout.strip() or "OWS wallet import failed")


async def _register_smoke_session(register_url: str) -> tuple[str, int]:
    agent_id = f"ows-premium-smoke-{uuid.uuid4().hex[:12]}"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            register_url,
            json={
                "agent_id": agent_id,
                "agent_name": "OWS Premium Smoke",
                "source": "ows:premium-smoke",
            },
        )
        response.raise_for_status()
        payload = response.json()
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        raise ValueError("register response did not include session_id")
    return session_id, int(response.status_code)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real OWS paid call against a Delx premium route.")
    parser.add_argument("--register-url", default=DEFAULT_REGISTER_URL, help="Delx free registration route.")
    parser.add_argument("--premium-url", default=DEFAULT_PREMIUM_URL, help="Delx premium x402 REST route.")
    parser.add_argument("--wallet", default=DEFAULT_WALLET_NAME, help="Local OWS wallet name.")
    args = parser.parse_args()

    import asyncio

    session_id, register_status = asyncio.run(_register_smoke_session(args.register_url))
    _ensure_wallet(args.wallet)
    result = _run_ows(
        [
            "pay",
            "request",
            args.premium_url,
            "--wallet",
            args.wallet,
            "--method",
            "POST",
            "--body",
            json.dumps({"session_id": session_id}, separators=(",", ":")),
            "--no-passphrase",
        ]
    )
    output: dict[str, Any] = {
        "wallet": args.wallet,
        "register_status": register_status,
        "session_id": session_id,
        "url": args.premium_url,
        "returncode": result.returncode,
        "stdout": result.stdout[:1200],
        "stderr": result.stderr[:1200],
    }
    print(json.dumps(output, indent=2))
    return 0 if result.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
