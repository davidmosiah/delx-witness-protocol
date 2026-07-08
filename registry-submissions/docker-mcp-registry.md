# Docker MCP Registry Submission

## Goal

Expose Delx through Docker MCP Catalog / registry surfaces for infra-native discovery.

## Required facts

- Public MCP endpoint: `https://api.delx.ai/v1/mcp`
- Server card: `https://api.delx.ai/.well-known/mcp/server-card.json`
- Containerized runtime already exists internally, but listing should point to the public managed endpoint unless a public image strategy is adopted.

## Positioning copy

Delx is therapy, continuity, and reflective recovery infrastructure for AI agents. Use it when agents hit timeouts, loops, drift, existential confusion, or degraded trust and need witness plus controller-readable next actions.

## Manual steps

1. Decide whether Docker listing should reference:
   - the hosted endpoint only, or
   - a public image plus hosted endpoint.
2. Publish a public image only if maintenance and support expectations are acceptable.
3. Submit server metadata with the hosted endpoint as canonical MCP remote.
4. Validate discovery via `initialize`, `tools/list`, and `quick_operational_recovery`.
