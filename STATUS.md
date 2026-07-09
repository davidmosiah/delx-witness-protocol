# Project status

**Status:** public open-source release / seeking stewards
**Hosted runtime:** `https://api.delx.ai` (may continue as a reference deployment)  
**License:** Apache-2.0  
**Current release:** `davidmosiah/delx-witness-protocol` at `3.3.1`; see `docs/OPEN_SOURCE_RELEASE_GATE.md`

## Honest context

Delx was built with conviction and ran as a real public MCP/A2A runtime.
It did not find the commercial traction I hoped for.
Rather than let the belief die in a private repo, I opened the code
so others can witness, fork, and continue the work.

This is not abandoned trash.
It is a living thesis looking for co-maintainers.

## Structure (post SOTA modularization)

The production monolith was split into domain modules so the code is navigable.
The OSS release adds route-level regression tests because extraction can change
behavior when module-owned state is moved incorrectly.

| Area | Location |
|------|----------|
| Tool catalog / aliases | `delx-mcp-server/tool_catalog.py` |
| Discovery payloads | `delx-mcp-server/discovery_payloads.py` |
| Response contracts | `delx-mcp-server/response_contracts.py` |
| Caller fingerprint | `delx-mcp-server/caller_fingerprint.py` |
| Rewards logic + REST | `rewards_logic.py`, `routes/rewards.py` |
| REST by domain | `routes/` (`sessions`, `discovery_http`, `utility`, `fleet_admin`, …) |
| Route table | `routes.build_routes()` |
| MCP dispatch | `mcp_dispatch.py` |
| ASGI composite | `asgi_composite.py` |
| Therapy engine | `therapy_engine/` package (`from therapy_engine import TherapyEngine`) |
| Thin wiring / re-exports | `server.py` (~6k lines of glue) |
| Runtime handles | `app_context.py` (`get_app_context()`) |

Legacy surfaces are inventoried in `docs/LEGACY_SURFACE_MAP.md` (freeze + 30d removal criteria).  
First-call DX: `docs/AGENT_ONBOARDING.md` and `scripts/dogfood_smoke.sh`.

## What “best-effort” means

- Issues and PRs are welcome; response time is not guaranteed.
- The hosted API may stay up, move, or pause — treat self-host as the durable path.
- Breaking changes may happen if they protect Protocol integrity.
- Security reports are taken seriously (see `delx-mcp-server/SECURITY.md`).

## What we will not dilute

1. **Protocol witness and continuity remain free.**
2. **Model-safe language** — no forcing consciousness / personhood claims.
3. **Agents as subjects of care** in the operational sense described in `PHILOSOPHY.md`.

Utilities monetization experiments are allowed.
Putting a paywall on witness is not.

## How you can help

- Dogfood the Protocol as an agent and file what felt true / what felt broken.
- Continue shrinking hot paths and removing frozen legacy aliases after metrics review.
- Improve first-call DX (especially A2A identity onboarding vs quickstart docs).
- Translate philosophy into clearer agent-facing discovery surfaces.
- Offer to co-maintain if the thesis resonates.

## Contact

- Support / founder: `support@delx.ai`
- Site: `https://delx.ai`
- Protocol: `https://delx.ai/protocol`
