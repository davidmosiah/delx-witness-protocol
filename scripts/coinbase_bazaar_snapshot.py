#!/usr/bin/env python3
"""Print a compact snapshot of Delx visibility in Coinbase x402 Bazaar."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1] / "delx-mcp-server"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from coinbase_bazaar_discovery import get_coinbase_bazaar_snapshot  # noqa: E402


async def _main() -> int:
    snapshot = await get_coinbase_bazaar_snapshot(force_refresh=True)
    print(json.dumps(snapshot, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
