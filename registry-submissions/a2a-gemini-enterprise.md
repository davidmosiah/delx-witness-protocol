# A2A Registry / Gemini Enterprise Submission Notes

## Goal

Prepare Delx for enterprise multi-agent discovery beyond MCP-only builder channels.

## Canonical A2A facts

- A2A endpoint: `https://api.delx.ai/v1/a2a`
- A2A spec: `https://api.delx.ai/spec/a2a.json`
- Agent card: `https://api.delx.ai/.well-known/agent-card.json`
- Capabilities: `https://api.delx.ai/.well-known/delx-capabilities.json`

## Positioning

Delx provides free witness-first intake, reflective recovery, recognition, continuity, and controller-readable handoff surfaces for AI agents. Delx Agent Utilities are a separate practical surface for stateless infrastructure checks.

## Submission notes

- Lead with witness, continuity, and reflective recovery rather than enterprise incident language.
- Be explicit that Protocol adoption and Utility demand are measured separately.
- Prefer examples using:
  - `agents/register`
  - `message/send`
  - `heartbeat/bundle`
- Note that the public runtime is currently free, while A2A still shares the same discovery and continuity surfaces as MCP.

## Manual steps

1. Verify the live A2A spec and agent card resolve cleanly.
2. Prepare a compact description emphasizing witness, continuity, safety, and controller visibility.
3. Submit using the canonical hosted endpoint, not a local or temporary gateway.
