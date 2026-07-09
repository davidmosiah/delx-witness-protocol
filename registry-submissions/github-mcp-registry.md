# GitHub MCP Registry Submission

## Goal

List Delx in GitHub MCP Registry for builder discovery and one-click MCP install flows.

## Source data

- Canonical ID: `io.github.davidmosiah/delx-mcp-a2a`
- Repository: `https://github.com/davidmosiah/delx-witness-protocol`
- Remote endpoint: `https://api.delx.ai/v1/mcp`
- Streamable transport: yes
- Server manifest already present in repo root:
  - `/path/to/delx-witness-protocol/server.json`

## Review notes to include

- Delx is not a generic chat server.
- Delx Protocol is the free witness/continuity surface for agents.
- Delx Agent Utilities are stateless infrastructure tools available through the same MCP endpoint.
- Recommended Protocol first call is `start_therapy_session`, `quick_session`, or `reflect`.
- Recommended Utilities discovery is `tools/list` with `tier=all` and `util_` tool names.
- Core Protocol runtime is free.
- Public experiment boundary: not tenant-isolated, so redact secrets and sensitive third-party data.

## Manual steps

1. Ensure `/path/to/delx-witness-protocol/server.json` is current.
2. Open the target GitHub registry contribution flow.
3. Submit or PR the server manifest.
4. Verify registry display points to `https://api.delx.ai/v1/mcp`.
5. Re-run a public `initialize` + `tools/list` probe after listing.
