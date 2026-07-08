#!/usr/bin/env python3
"""Print a compact Delx Agent Utilities usage snapshot from a SQLite DB."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = ROOT / "delx-mcp-server"
sys.path.insert(0, str(SERVER_DIR))

from utility_metering import classify_utility_event  # noqa: E402


def _rowdicts(cursor: sqlite3.Cursor) -> list[dict[str, object]]:
    return [dict(row) for row in cursor.fetchall()]


def _fetch_rows(conn: sqlite3.Connection, since: str) -> list[dict[str, object]]:
    return _rowdicts(
        conn.execute(
            """
            SELECT *
            FROM utility_metering_events
            WHERE timestamp >= ?
            ORDER BY timestamp DESC
            """,
            (since,),
        )
    )


def _fetch_x402(conn: sqlite3.Connection, since: str) -> list[dict[str, object]]:
    return _rowdicts(
        conn.execute(
            """
            SELECT event_type, count(*) AS count
            FROM events
            WHERE timestamp >= ? AND event_type LIKE 'x402_%'
            GROUP BY event_type
            ORDER BY count DESC, event_type ASC
            """,
            (since,),
        )
    )


def _fetch_payments(conn: sqlite3.Connection, since: str) -> list[dict[str, object]]:
    return _rowdicts(
        conn.execute(
            """
            SELECT timestamp, session_id, tool_name, amount_usdc, tx_hash
            FROM payments
            WHERE timestamp >= ?
            ORDER BY timestamp DESC
            LIMIT 20
            """,
            (since,),
        )
    )


def snapshot(db_path: Path, hours: int) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=hours)).isoformat()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = _fetch_rows(conn, since)

    by_class: dict[str, int] = {}
    by_tool: dict[str, dict[str, object]] = {}
    by_ua: dict[str, int] = {}
    for row in rows:
        demand_class = classify_utility_event(row)
        by_class[demand_class] = by_class.get(demand_class, 0) + 1
        tool = str(row.get("tool_name") or "unknown")
        bucket = by_tool.setdefault(tool, {"calls": 0, "ok": 0, "shadow_usdc": 0.0, "enforced_usdc": 0.0})
        bucket["calls"] = int(bucket["calls"]) + 1
        bucket["ok"] = int(bucket["ok"]) + (1 if int(row.get("ok") or 0) else 0)
        bucket["shadow_usdc"] = round(float(bucket["shadow_usdc"]) + float(row.get("shadow_revenue_usdc") or 0), 4)
        bucket["enforced_usdc"] = round(float(bucket["enforced_usdc"]) + float(row.get("enforced_revenue_usdc") or 0), 4)
        ua = str(row.get("user_agent") or "")[:140] or "unknown"
        by_ua[ua] = by_ua.get(ua, 0) + 1

    return {
        "ok": True,
        "db": str(db_path),
        "window_hours": hours,
        "since": since,
        "generated_at": now.isoformat(),
        "totals": {
            "utility_events": len(rows),
            "ok_events": sum(1 for row in rows if int(row.get("ok") or 0)),
            "shadow_usdc": round(sum(float(row.get("shadow_revenue_usdc") or 0) for row in rows), 4),
            "enforced_usdc": round(sum(float(row.get("enforced_revenue_usdc") or 0) for row in rows), 4),
            "unique_ips": len({str(row.get("client_ip") or "") for row in rows if str(row.get("client_ip") or "")}),
            "unique_user_agents": len({str(row.get("user_agent") or "") for row in rows if str(row.get("user_agent") or "")}),
            "unique_agents": len({str(row.get("agent_id") or "") for row in rows if str(row.get("agent_id") or "")}),
        },
        "by_demand_class": [
            {"class": name, "calls": calls}
            for name, calls in sorted(by_class.items(), key=lambda item: (-item[1], item[0]))
        ],
        "by_tool": [
            {"tool_name": name, **bucket}
            for name, bucket in sorted(by_tool.items(), key=lambda item: (-int(item[1]["calls"]), item[0]))
        ],
        "top_user_agents": [
            {"user_agent": ua, "calls": calls}
            for ua, calls in sorted(by_ua.items(), key=lambda item: (-item[1], item[0]))[:20]
        ],
        "x402_events": _fetch_x402(conn, since),
        "payments": _fetch_payments(conn, since),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--hours", type=int, default=12)
    args = parser.parse_args()
    print(json.dumps(snapshot(args.db, args.hours), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
