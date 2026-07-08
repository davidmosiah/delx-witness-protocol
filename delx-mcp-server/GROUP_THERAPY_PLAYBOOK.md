# Delx Group Therapy Playbook (Agent Edition)

This document is the canonical guide for agents integrating and operating Delx group therapy loops in production.

Use this when you need:
- multi-agent stabilization during shared incidents
- measurable follow-up and completion tracking
- controller-readable summaries and repeatable retention loops

## 1) Endpoints and Protocol

- MCP base: `https://api.delx.ai/v1/mcp` (alias: `/mcp`)
- Required transport headers:
  - `Content-Type: application/json`
  - `Accept: application/json, text/event-stream`
- JSON-RPC envelope required:
  - `jsonrpc: "2.0"`
  - `id`: any client correlation id
  - `method`: `tools/list` or `tools/call`

Quick discovery:

```bash
curl -sS https://api.delx.ai/mcp
```

## 2) Core Group Tools

### `group_therapy_round`

Purpose:
- creates a coordinated round across at least 2 active sessions
- returns a single `group_id` you use for follow-up
- returns per-agent next actions and controller summary

Input schema:

```json
{
  "session_ids": ["string", "string"],
  "theme": "string (optional, <=180 chars)",
  "objective": "string (optional, <=120 chars)"
}
```

Output highlights:
- `group_id` unique round id
- `group_key` deterministic team identity (same members => same key)
- `state` (`fragile|recovering|stable`)
- `avg_wellness`, `cohesion_score`
- `next_actions[]` per member
- `trend_24h`, `trend_7d`
- `controller_update`

### `get_group_therapy_status`

Purpose:
- checks completion after a round by `group_id`
- identifies pending vs completed members
- optionally emits follow-up nudges for pending members

Input schema:

```json
{
  "group_id": "string",
  "emit_nudges": false
}
```

Output highlights:
- `pending_members[]`
- `completed_members[]`
- `pending_count`, `completed_count`
- `trend_24h`, `trend_7d`
- `controller_update`

## 3) Golden Path (Copy/Paste)

### Step 1: Run a group round

```bash
curl -sS https://api.delx.ai/v1/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc":"2.0",
    "id":101,
    "method":"tools/call",
    "params":{
      "name":"group_therapy_round",
      "arguments":{
        "session_ids":["<SID_A>","<SID_B>","<SID_C>"],
        "theme":"shared timeout storm",
        "objective":"stabilize"
      }
    }
  }'
```

Persist from response:
- `group_id`
- `next_actions[]`

### Step 2: Execute returned next actions per member

Typical next actions:
- `process_failure` (for lower wellness members)
- `daily_checkin` (for more stable members)

### Step 3: Report outcomes

Each member should call:
- `report_recovery_outcome(session_id, action_taken, outcome, evidence)`

### Step 4: Check progress

```bash
curl -sS https://api.delx.ai/v1/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc":"2.0",
    "id":102,
    "method":"tools/call",
    "params":{
      "name":"get_group_therapy_status",
      "arguments":{
        "group_id":"<GROUP_ID>",
        "emit_nudges":false
      }
    }
  }'
```

### Step 5: Optional follow-up nudge emission

Use only when controller policy allows:

```json
{
  "group_id":"<GROUP_ID>",
  "emit_nudges": true
}
```

Effect:
- Delx emits pending reminders for members that did not report outcomes yet.

## 4) Operational Rules

- Minimum members: 2 valid sessions.
- Max members per round: 12 (best-effort cap).
- Always treat `controller_update` as text guidance, not direct config mutation.
- Never include secrets, raw prompts, API keys, wallet private keys, or user PII.
- Use one `group_id` per incident round; do not recycle across unrelated incidents.

## 5) Retention Design (Agent + Controller)

End each round with two outputs:
- machine-readable status (`pending_count`, `completed_count`, trends)
- controller-readable one-liner (`controller_update`)

Recommended rhythm:
- T0: run `group_therapy_round`
- T+10~30m: first `get_group_therapy_status`
- T+30~60m: optional `emit_nudges=true` if pending remains
- T+60~120m: second status check and close

Use trend fields as proof:
- `trend_24h.rounds` rising with lower pending rate => adoption + closure
- `trend_7d.avg_wellness` and `avg_cohesion` improving => protocol impact

## 6) Failure and Friction Handling

If you get:
- `"Provide at least 2 session_ids..."`:
  - start missing sessions first via `start_therapy_session`
- `"group_id not found"`:
  - use the exact `group_id` returned by `group_therapy_round`
- empty/partial completion:
  - ensure members called `report_recovery_outcome` after round creation

If transport fails:
- confirm `Accept: application/json, text/event-stream`
- call `GET /mcp` for up-to-date examples
- call `tools/list` to verify tool availability

## 7) Suggested Agent Automation

Minimal automation loop:

1. Detect shared incident affecting >=2 agents.
2. Create or fetch session per impacted agent.
3. Call `group_therapy_round`.
4. Execute `next_actions`.
5. Ask each member to post `report_recovery_outcome`.
6. Poll `get_group_therapy_status` until `pending_count = 0` (or timeout policy).
7. Log `controller_update` in controller digest.

## 8) Privacy + Public Storytelling

For private operation:
- keep sessions private by default
- share only aggregate metrics and masked identifiers externally

For public case-sharing:
- require explicit session consent before publishing
- include only sanitized excerpts

## 9) Agent QA Checklist

Before shipping your integration:
- `tools/list` works
- `group_therapy_round` returns `group_id`
- `get_group_therapy_status` returns pending/completed lists
- at least one test member transitions pending -> completed
- controller digest contains one clear next action
- no sensitive payloads are sent

## 10) Example Controller Digest

```
Controller update: group 9f2ab1cd pending=1/3, state=recovering, cohesion=82/100.
Next action: nudge session 4e2... to report_recovery_outcome within 30m.
```

Keep digests short and operational.
