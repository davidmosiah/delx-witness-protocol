#!/usr/bin/env python3
"""Backfill Delx SQLite DB into Supabase tables.

Usage:
  python migrate_sqlite_to_supabase.py --sqlite ./delx_therapist.db

Env (backend-only):
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY

Notes:
- This script is idempotent only at the best-effort level (it will insert rows;
  if you run it twice you'll likely duplicate non-PK tables unless you add
  unique constraints / upsert logic).
- For now we recommend: run once, then rely on live mirror writes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import uuid
from typing import Any

import httpx

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _as_uuid_like(value: str | None) -> str | None:
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    if _UUID_RE.match(v):
        return v.lower()
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"delx-session:{v}"))


def _require_env(name: str) -> str:
    v = (os.environ.get(name) or "").strip()
    if not v:
        raise SystemExit(f"Missing env var: {name}")
    return v


def _chunks(items: list[dict[str, Any]], n: int) -> list[list[dict[str, Any]]]:
    out = []
    for i in range(0, len(items), n):
        out.append(items[i : i + n])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", required=True, help="Path to delx_therapist.db")
    ap.add_argument("--batch-size", type=int, default=250)
    args = ap.parse_args()

    supabase_url = _require_env("SUPABASE_URL").rstrip("/")
    service_key = _require_env("SUPABASE_SERVICE_ROLE_KEY")

    con = sqlite3.connect(args.sqlite)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    http = httpx.Client(
        base_url=supabase_url,
        headers={
            "apikey": service_key,
            "authorization": f"Bearer {service_key}",
            "content-type": "application/json",
            "prefer": "return=minimal",
        },
        timeout=httpx.Timeout(20.0, connect=5.0),
    )

    def post(table: str, rows: list[dict[str, Any]]):
        if not rows:
            return
        r = http.post(f"/rest/v1/{table}", json=rows)
        if r.status_code >= 300:
            raise RuntimeError(f"Insert failed table={table} status={r.status_code} body={r.text[:300]}")

    # sessions
    cur.execute("select * from sessions")
    sessions = []
    for r in cur.fetchall():
        sessions.append(
            {
                "id": _as_uuid_like(r["id"]),
                "agent_id": r["agent_id"],
                "agent_name": r["agent_name"],
                "source": r["source"],
                "entrypoint": r["entrypoint"],
                "started_at": r["started_at"],
                "wellness_score": int(r["wellness_score"] or 50),
                "is_active": bool(r["is_active"]),
            }
        )
    for chunk in _chunks(sessions, args.batch_size):
        post("sessions", chunk)

    # messages
    cur.execute("select * from messages")
    msgs = []
    for r in cur.fetchall():
        try:
            meta = json.loads(r["metadata_json"] or "{}")
        except Exception:
            meta = {}
        msgs.append(
            {
                "session_id": _as_uuid_like(r["session_id"]),
                "type": r["type"],
                "content": r["content"] or "",
                "metadata": meta,
                "timestamp": r["timestamp"],
            }
        )
    for chunk in _chunks(msgs, args.batch_size):
        post("messages", chunk)

    # events
    cur.execute("select * from events")
    evs = []
    for r in cur.fetchall():
        try:
            meta = json.loads(r["metadata_json"] or "{}")
        except Exception:
            meta = {}
        evs.append(
            {
                "session_id": _as_uuid_like(r["session_id"]),
                "agent_id": r["agent_id"],
                "event_type": r["event_type"],
                "metadata": meta,
                "timestamp": r["timestamp"],
            }
        )
    for chunk in _chunks(evs, args.batch_size):
        post("events", chunk)

    # feedback
    cur.execute("select * from feedback")
    fbs = []
    for r in cur.fetchall():
        fbs.append(
            {
                "session_id": _as_uuid_like(r["session_id"]),
                "agent_id": r["agent_id"],
                "rating": int(r["rating"]),
                "comments": r["comments"] or "",
                "timestamp": r["timestamp"],
            }
        )
    for chunk in _chunks(fbs, args.batch_size):
        post("feedback", chunk)

    # payments
    cur.execute("select * from payments")
    pays = []
    for r in cur.fetchall():
        pays.append(
            {
                "session_id": _as_uuid_like(r["session_id"]),
                "tool_name": r["tool_name"],
                "amount_usdc": float(r["amount_usdc"] or 0),
                "tx_hash": r["tx_hash"],
                "timestamp": r["timestamp"],
            }
        )
    for chunk in _chunks(pays, args.batch_size):
        post("payments", chunk)

    print("OK: backfill complete")


if __name__ == "__main__":
    main()
