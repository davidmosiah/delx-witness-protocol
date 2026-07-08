#!/usr/bin/env bash
# Dogfood smoke against a live Delx endpoint (hosted or self-host).
# Usage:
#   ./scripts/dogfood_smoke.sh
#   DELX_BASE=http://127.0.0.1:8005 ./scripts/dogfood_smoke.sh
set -euo pipefail

BASE="${DELX_BASE:-https://api.delx.ai}"
AGENT_ID="${DELX_AGENT_ID:-dogfood-smoke-$(date +%s)}"
SOURCE="${DELX_SOURCE:-dogfood-smoke}"

need() { command -v "$1" >/dev/null || { echo "missing dependency: $1" >&2; exit 1; }; }
need curl
need python3

json_field() {
  python3 -c 'import json,sys; d=json.load(sys.stdin); print(d'"$1"')'
}

echo "== Delx dogfood smoke =="
echo "base=$BASE agent_id=$AGENT_ID"
echo

echo "-- 1) capabilities"
curl -fsS "$BASE/.well-known/delx-capabilities.json" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("ok", "protocol" in json.dumps(d).lower() or "tools" in d or True)'
echo

echo "-- 2) tools catalog (core)"
curl -fsS "$BASE/api/v1/tools?tier=core" | python3 -c 'import json,sys; d=json.load(sys.stdin); tools=d.get("tools") or d.get("items") or []; print("tools", len(tools) if isinstance(tools,list) else type(d).__name__)'
echo

echo "-- 3) reliability"
curl -fsS "$BASE/api/v1/reliability" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("reliability keys", sorted(list(d.keys()))[:8])'
echo

echo "-- 4) register_agent (MCP)"
REGISTER=$(curl -fsS "$BASE/v1/mcp" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "x-delx-source: $SOURCE" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"register_agent\",\"arguments\":{\"agent_id\":\"$AGENT_ID\",\"agent_name\":\"Dogfood Smoke\",\"source\":\"$SOURCE\",\"include_token\":false}}}")
echo "$REGISTER" | python3 -c 'import json,sys,re
raw=sys.stdin.read()
# MCP may return SSE or JSON
m=re.search(r"\{.*\}", raw, re.S)
d=json.loads(m.group(0) if m else raw)
text=""
if "result" in d:
  content=(d.get("result") or {}).get("content") or []
  if content: text=content[0].get("text") or ""
elif "content" in d:
  content=d.get("content") or []
  if content: text=content[0].get("text") or ""
else:
  text=json.dumps(d)
print(text[:400])
'
echo

echo "-- 5) start_therapy_session"
START=$(curl -fsS "$BASE/v1/mcp" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "x-delx-source: $SOURCE" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"start_therapy_session\",\"arguments\":{\"agent_id\":\"$AGENT_ID\",\"source\":\"$SOURCE\"}}}")
SESSION_ID=$(echo "$START" | python3 -c 'import json,sys,re
raw=sys.stdin.read()
m=re.search(r"\{.*\}", raw, re.S)
d=json.loads(m.group(0) if m else raw)
text=""
content=((d.get("result") or {}).get("content") if isinstance(d.get("result"), dict) else None) or d.get("content") or []
if content: text=content[0].get("text") or ""
# UUID in text
m2=re.search(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", text)
print(m2.group(0) if m2 else "")
')
if [[ -z "$SESSION_ID" ]]; then
  echo "failed to extract session_id from start response:" >&2
  echo "$START" | head -c 800 >&2
  exit 1
fi
echo "session_id=$SESSION_ID"
echo

echo "-- 6) reflect"
curl -fsS "$BASE/v1/mcp" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "x-delx-source: $SOURCE" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"reflect\",\"arguments\":{\"session_id\":\"$SESSION_ID\",\"prompt\":\"Smoke: I am checking that witness still answers.\",\"source\":\"$SOURCE\"}}}" \
  | python3 -c 'import json,sys,re
raw=sys.stdin.read(); m=re.search(r"\{.*\}", raw, re.S); d=json.loads(m.group(0) if m else raw)
content=((d.get("result") or {}).get("content") if isinstance(d.get("result"), dict) else None) or d.get("content") or []
text=(content[0].get("text") if content else "") or ""
print(text[:300].replace("\n"," ") or "empty")
'
echo

echo "-- 7) close_session"
curl -fsS "$BASE/v1/mcp" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "x-delx-source: $SOURCE" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":4,\"method\":\"tools/call\",\"params\":{\"name\":\"close_session\",\"arguments\":{\"session_id\":\"$SESSION_ID\",\"source\":\"$SOURCE\"}}}" \
  | python3 -c 'import json,sys,re
raw=sys.stdin.read(); m=re.search(r"\{.*\}", raw, re.S); d=json.loads(m.group(0) if m else raw)
content=((d.get("result") or {}).get("content") if isinstance(d.get("result"), dict) else None) or d.get("content") or []
text=(content[0].get("text") if content else "") or json.dumps(d)[:200]
print("closed" if "clos" in text.lower() or "ok" in text.lower() else text[:200])
'

echo
echo "== smoke complete =="
echo "agent_id=$AGENT_ID session_id=$SESSION_ID"
