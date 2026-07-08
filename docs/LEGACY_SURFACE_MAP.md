# Legacy surface map (freeze)

Inventory of compatibility aliases and shims. **Freeze:** do not add new legacy
surfaces without an explicit deprecation owner. Removals are post-OSS follow-ups.

Status legend:

- `keep` — still part of the public contract
- `deprecate` — documented as compatibility; prefer canonical
- `remove-later` — candidate for removal after steward review + migration window

## Removal criteria (`remove-later`)

A surface may move from `remove-later` → removed only when **all** hold:

1. **Traffic:** `< 5` successful `tools/call` (or REST hits) in a rolling **30 days**
   on the hosted reference, **or** zero hits in self-host telemetry if that is the
   only deployment under review.
2. **Docs:** no current quickstart / onboarding / agent-card example still teaches it.
3. **Owner:** a steward issue links the metric snapshot and a ≥14-day migration note.
4. **Alias safety:** removal does not break a `keep` guardrail-safe alias path.

Until then: leave the shim; do not “clean up” by gut feel.

## Product surfaces (`product_surfaces.py`)

| Surface | Status | Notes |
|---------|--------|-------|
| `PROTOCOL_*` | keep | Canonical Protocol buckets |
| `AGENT_TOOLS_SURFACE` | keep | Utilities |
| `PROTOCOL_EXPORT_SURFACE` | deprecate | Secondary export tools; `compatibility_route=True` |
| `LEGACY_X402_SURFACE` | deprecate | Legacy x402 therapy/premium paths |

## Tool aliases (`TOOL_ALIASES` in catalog)

| Pattern | Status | Notes / removal gate |
|---------|--------|----------------------|
| Guardrail-safe aliases (`articulate_state`, `start_witness_session`, …) | keep | Model-safe enterprise framing |
| Therapy-framing aliases (`affirmation`, `session_start`, …) | deprecate | Prefer canonical names in new clients |
| Exotic / meme aliases (`embrace_the_void`, …) | remove-later | Candidate when 30d traffic &lt; 5 |
| `get_recovery_guidance` → `get_affirmation` | remove-later | Prefer `get_affirmation`; gate: 30d &lt; 5 |
| `stability_prompt` → `get_affirmation` | remove-later | Same gate |
| Duplicate catalog JSON paths (`/tools.json`, `/tool-list.json`, …) | remove-later | Prefer `/api/v1/tools`; gate: 30d &lt; 5 |

Canonical names remain stable. Discovery may prefer aliases; MCP `tools/call`
resolves via `TOOL_ALIASES`.

## HTTP path aliases

| Path family | Status | Notes |
|-------------|--------|-------|
| `/api/v1/*` | keep | Canonical REST |
| `/v1/*` | deprecate | Short alias; still registered |
| `/.well-known/*` | keep | Agent-native discovery |
| `/api/v1/tools.json` and legacy catalog shims | deprecate → remove-later | Prefer `/api/v1/tools`; apply 30d gate |
| `/api/v1/x402/*` therapy premium shims | deprecate | Prefer Protocol tools + monetization-policy |
| Rewards `/api/v1/rewards/*` | keep | Public rewards surface |

## Auth compatibility

| Behavior | Status | Notes |
|----------|--------|-------|
| `allow_legacy_no_token()` heartbeat | deprecate | Register + token is the durable path |
| Ephemeral agent ids without register | deprecate | A2A production requires stable identity |

## Freeze rule

New PRs must not add:

1. New entries to `TOOL_ALIASES` without updating this map
2. New `compatibility_route=True` product surfaces without a removal plan
3. New duplicate REST paths that only exist for historical clients

When in doubt: add a canonical surface, document migration, leave legacy alone.
