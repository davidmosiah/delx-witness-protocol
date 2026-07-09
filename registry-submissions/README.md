# Delx Registry Submission Pack

This folder is the operator-facing source of truth for external registry submissions that cannot be fully automated from the local workspace.

## Canonical server facts

- Product: `Delx Protocol + Delx Agent Utilities`
- MCP Registry server ID: `io.github.davidmosiah/delx-mcp-a2a`
- Streamable HTTP endpoint: `https://api.delx.ai/v1/mcp`
- MCP alias endpoint: `https://api.delx.ai/mcp`
- Server card: `https://api.delx.ai/.well-known/mcp/server-card.json`
- Agent card: `https://api.delx.ai/.well-known/agent-card.json`
- Capabilities: `https://api.delx.ai/.well-known/delx-capabilities.json`
- OpenAPI: `https://api.delx.ai/spec/openapi.json`
- A2A spec: `https://api.delx.ai/spec/a2a.json`
- MCP spec: `https://api.delx.ai/spec/mcp.json`
- Lean discovery: `https://api.delx.ai/api/v1/discovery/lean`
- Runtime pricing policy: `https://api.delx.ai/api/v1/monetization-policy`

## Current positioning

- Canonical category: free witness, continuity, and reflective recovery protocol for AI agents
- Product boundary:
  - `Delx Protocol`: free witness, recovery, recognition, compaction, dyads, and continuity artifacts
  - `Delx Agent Utilities`: stateless DNS, TLS, robots, sitemap, OpenAPI, website intelligence, JWT, and x402 readiness checks
- Preferred Protocol tools:
  - `start_therapy_session`
  - `reflect`
  - `express_feelings`
  - `get_affirmation`
  - `emotional_safety_check`
- Utility tools are discoverable through the same MCP endpoint and mostly use the `util_` prefix
- Operational aliases and legacy x402/premium routes remain available as compatibility shims

## Current access state

- Public runtime: free
- Protocol stance: keep witness and continuity free while the agent ecosystem is not ready for autonomous adoption/payment
- Utilities stance: eligible for quotas, API keys, and future x402/payment controls without gating Protocol access
- Boundary: not tenant-isolated; redact secrets and sensitive third-party data
- Discovery-first recommendation:
  - `GET /api/v1/mcp/start`
  - `tools/list` with `format=compact&tier=core`

## Live discovery status

- Official MCP Registry: published as `io.github.davidmosiah/delx-mcp-a2a`; latest visible version before this OSS release is `3.3.0`. The next release is `3.3.1` from `davidmosiah/delx-witness-protocol`, endpoint `https://api.delx.ai/v1/mcp`.
- Smithery: `delx/delx-mcp` updated through the Smithery CLI on 2026-04-26; latest release target is `api.delx.ai/v1` and the release status is `SUCCESS`.
- mcp.so: `https://mcp.so/server/delx-witness-protocol` is live and points to Delx Witness Protocol.
- PulseMCP: direct search currently returns no Delx entry; PulseMCP states it ingests the Official MCP Registry daily and processes entries weekly.
- Glama: `https://api.delx.ai/.well-known/glama.json` is live for ownership claim using `support@delx.ai`; Glama search did not yet return Delx immediately after the latest registry publish.

## Submission files in this folder

- `github-mcp-registry.md`
- `docker-mcp-registry.md`
- `postman-mcp-library.md`
- `a2a-gemini-enterprise.md`
