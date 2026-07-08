# Postman MCP Library Submission

## Goal

List Delx where API builders and agent integrators already evaluate external services.

## Assets

- MCP spec: `https://api.delx.ai/spec/mcp.json`
- OpenAPI: `https://api.delx.ai/spec/openapi.json`
- Lean discovery: `https://api.delx.ai/api/v1/discovery/lean`
- Reliability surface: `https://api.delx.ai/api/v1/reliability`

## Suggested headline

Delx Protocol + Agent Utilities

## Suggested description

Free witness, continuity, and reflective recovery protocol for AI agents, plus stateless agent utilities for DNS, TLS, web, OpenAPI, robots, sitemap, and x402 readiness checks.

## Manual steps

1. Import the live specs from the canonical URLs.
2. Attach example requests for:
   - `initialize`
   - `tools/list` with `format=lean`
   - `start_therapy_session`
   - `reflect`
   - one `util_` tool such as `util_dns_lookup` or `util_tls_inspect`
3. Include the public runtime note:
   - Delx Protocol access is free
   - Utilities may later use quotas/API keys/x402 without gating Protocol access
   - redact secrets and sensitive third-party data
