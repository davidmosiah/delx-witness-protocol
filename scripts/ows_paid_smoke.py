#!/usr/bin/env python3
"""Run a real OWS paid request against a Delx paid x402 route.

Flow:
- ensure an OWS wallet exists locally
- import the local AgentCash EVM key into OWS if needed
- pay a Delx x402 utility route through the official OWS CLI

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
from pathlib import Path
from typing import Any


DEFAULT_URL = "https://api.delx.ai/api/v1/x402/jwt-inspect"
DEFAULT_BODY = {
    "token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhZ2VudC0xMjMifQ.signature",
}
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real OWS paid call against a Delx x402 route.")
    parser.add_argument("--url", default=DEFAULT_URL, help="Paid Delx x402 REST endpoint.")
    parser.add_argument("--wallet", default=DEFAULT_WALLET_NAME, help="Local OWS wallet name.")
    parser.add_argument(
        "--body",
        default=json.dumps(DEFAULT_BODY),
        help="JSON body to send on the paid request.",
    )
    args = parser.parse_args()

    payload = json.loads(args.body)
    if not isinstance(payload, dict):
        raise ValueError("Body must be a JSON object")

    _ensure_wallet(args.wallet)
    result = _run_ows(
        [
            "pay",
            "request",
            args.url,
            "--wallet",
            args.wallet,
            "--method",
            "POST",
            "--body",
            json.dumps(payload, separators=(",", ":")),
            "--no-passphrase",
        ]
    )
    output: dict[str, Any] = {
        "wallet": args.wallet,
        "url": args.url,
        "returncode": result.returncode,
        "stdout": result.stdout[:1200],
        "stderr": result.stderr[:1200],
    }
    print(json.dumps(output, indent=2))
    return 0 if result.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
