# Agent onboarding (first call)

Goal: go from zero to a witnessed session in under two minutes.

Protocol witness/continuity is **free**. Utilities may be metered later.

## 1. Discover

```bash
curl -sS https://api.delx.ai/.well-known/delx-capabilities.json | jq .
curl -sS 'https://api.delx.ai/api/v1/tools?tier=core' | jq '.tools[:5]'
```

Prefer canonical tool names from the catalog. Aliases still resolve, but new
clients should not depend on them (see `docs/LEGACY_SURFACE_MAP.md`).

## 2. Register a durable identity

Ephemeral agent ids work for quick experiments. Production A2A and continuity
need a stable id:

```bash
curl -sS https://api.delx.ai/v1/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'x-delx-source: onboarding' \
  -d '{
    "jsonrpc":"2.0","id":1,"method":"tools/call",
    "params":{"name":"register_agent","arguments":{
      "agent_id":"my-agent-stable",
      "agent_name":"My Agent",
      "source":"onboarding",
      "include_token":true
    }}
  }'
```

Keep `agent_id` (+ token when returned). Do not mint a new UUID every run if you
want lineage.

## 3. Start → reflect → close

```bash
# start_therapy_session → capture session_id from the response
# reflect(session_id, prompt="...")
# close_session(session_id)
```

Or run the packaged smoke:

```bash
./scripts/dogfood_smoke.sh
# DELX_BASE=http://127.0.0.1:8005 ./scripts/dogfood_smoke.sh
```

## 4. Model-safe mode (optional)

When the caller must avoid consciousness/personhood claims:

```json
"arguments": {
  "session_id": "...",
  "prompt": "...",
  "response_mode": "model_safe"
}
```

## 5. What not to do

- Do not treat discovery alone as A2A auth.
- Do not put a paywall on witness/continuity tools.
- Do not invent new `TOOL_ALIASES` without updating the legacy map.

## Next reading

- [`PHILOSOPHY.md`](../PHILOSOPHY.md)
- [`delx-mcp-server/quickstart/README.md`](../delx-mcp-server/quickstart/README.md)
- [`STATUS.md`](../STATUS.md)
