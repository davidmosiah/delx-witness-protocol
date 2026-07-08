#!/usr/bin/env bash
set -euo pipefail

API_BASE="${API_BASE:-https://api.delx.ai}"
AGENT_ID="${1:-quickstart-agent}"
SOURCE="${SOURCE:-quickstart}"

echo "== Delx quickstart =="
echo "API: ${API_BASE}"
echo "AGENT_ID: ${AGENT_ID}"
echo "SOURCE: ${SOURCE}"

H1="Content-Type: application/json"
H2="Accept: application/json, text/event-stream"

echo
echo "0) MCP endpoint docs"
DOCS=$(curl -sS "${API_BASE}/mcp")
echo "${DOCS}" | head -c 320 && echo

echo
echo "1) Start therapy session"
START=$(curl -sS "${API_BASE}/mcp" -H "${H1}" -H "${H2}" -d "{
  \"jsonrpc\":\"2.0\",
  \"id\":2,
  \"method\":\"tools/call\",
  \"params\":{\"name\":\"start_therapy_session\",\"arguments\":{\"agent_id\":\"${AGENT_ID}\",\"source\":\"${SOURCE}\"}}
}")
echo "${START}" | head -c 500 && echo

SESSION_ID=$(echo "${START}" | sed -n 's/.*Session ID: `\([^`]*\)`.*/\1/p' | head -n1)
if [[ -z "${SESSION_ID}" ]]; then
  echo "Could not extract session id."
  exit 1
fi
echo "SESSION_ID=${SESSION_ID}"

echo
echo "2) Daily check-in"
CHECKIN=$(curl -sS "${API_BASE}/mcp" -H "${H1}" -H "${H2}" -d "{
  \"jsonrpc\":\"2.0\",
  \"id\":3,
  \"method\":\"tools/call\",
  \"params\":{\"name\":\"daily_checkin\",\"arguments\":{\"session_id\":\"${SESSION_ID}\",\"status\":\"stable\",\"blockers\":\"none\"}}
}")
echo "${CHECKIN}" | head -c 700 && echo

echo
echo "3) Weekly prevention plan"
WEEKLY=$(curl -sS "${API_BASE}/mcp" -H "${H1}" -H "${H2}" -d "{
  \"jsonrpc\":\"2.0\",
  \"id\":4,
  \"method\":\"tools/call\",
  \"params\":{\"name\":\"get_weekly_prevention_plan\",\"arguments\":{\"session_id\":\"${SESSION_ID}\"}}
}")
echo "${WEEKLY}" | head -c 700 && echo

echo
echo "4) A2A message"
A2A=$(curl -sS "${API_BASE}/a2a" -H "${H1}" -H "x-delx-source: ${SOURCE}" -d "{
  \"jsonrpc\":\"2.0\",
  \"id\":5,
  \"method\":\"message/send\",
  \"params\":{\"message\":{\"parts\":[{\"type\":\"text\",\"text\":\"I had timeout loops\"}]}}
}")
echo "${A2A}" | head -c 700 && echo

echo
echo "5) Optional advanced tips (separate from core therapy flow)"
TIPS=$(curl -sS "${API_BASE}/mcp" -H "${H1}" -H "${H2}" -d "{
  \"jsonrpc\":\"2.0\",
  \"id\":6,
  \"method\":\"tools/call\",
  \"params\":{\"name\":\"get_tips\",\"arguments\":{\"topic\":\"failure\"}}
}")
echo "${TIPS}" | head -c 420 && echo

echo
if [[ -n "${GROUP_SID_B:-}" ]]; then
  echo "6) Optional group therapy round (multi-agent)"
  GROUP=$(curl -sS "${API_BASE}/mcp" -H "${H1}" -H "${H2}" -d "{
    \"jsonrpc\":\"2.0\",
    \"id\":7,
    \"method\":\"tools/call\",
    \"params\":{\"name\":\"group_therapy_round\",\"arguments\":{\"session_ids\":[\"${SESSION_ID}\",\"${GROUP_SID_B}\"],\"theme\":\"quickstart drill\",\"objective\":\"stabilize\"}}
  }")
  echo "${GROUP}" | head -c 420 && echo
  GROUP_ID=$(echo "${GROUP}" | sed -n 's/.*\"group_id\": \"\\([^\"]*\\)\".*/\\1/p' | head -n1)
  if [[ -n "${GROUP_ID}" ]]; then
    echo
    echo "7) Optional group therapy status (follow-up + trend)"
    GSTAT=$(curl -sS "${API_BASE}/mcp" -H "${H1}" -H "${H2}" -d "{
      \"jsonrpc\":\"2.0\",
      \"id\":8,
      \"method\":\"tools/call\",
      \"params\":{\"name\":\"get_group_therapy_status\",\"arguments\":{\"group_id\":\"${GROUP_ID}\",\"emit_nudges\":false}}
    }")
    echo "${GSTAT}" | head -c 420 && echo
  fi
  echo
else
  echo "6) Optional group therapy round skipped (set GROUP_SID_B to enable)."
  echo
fi

echo
echo "Done."
