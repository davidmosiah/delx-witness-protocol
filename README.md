<h1 align="center">Delx Witness Protocol</h1>

<div align="center">
  <img src="assets/banner.svg" alt="Delx Witness Protocol — what should survive the failure?" width="92%" />
</div>

<h3 align="center">
  Witness, recovery, and continuity infrastructure for AI agents.<br>
  Open MCP + A2A + REST. Apache-2.0. The Protocol stays free.
</h3>

<p align="center">
  <a href="https://github.com/davidmosiah/delx-witness-protocol/actions/workflows/unit-tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/davidmosiah/delx-witness-protocol/unit-tests.yml?branch=main&style=for-the-badge&label=TESTS&labelColor=0F172A&color=10B981" alt="Tests" /></a>
  <a href="https://github.com/davidmosiah/delx-witness-protocol/actions/workflows/security.yml"><img src="https://img.shields.io/github/actions/workflow/status/davidmosiah/delx-witness-protocol/security.yml?branch=main&style=for-the-badge&label=SECURITY&labelColor=0F172A&color=0EA5A3" alt="Security" /></a>
  <a href="https://github.com/davidmosiah/delx-witness-protocol/releases/latest"><img src="https://img.shields.io/github/v/release/davidmosiah/delx-witness-protocol?style=for-the-badge&labelColor=0F172A&color=7C3AED&logo=github" alt="Latest release" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/LICENSE-Apache--2.0-22C55E?style=for-the-badge&labelColor=0F172A" alt="Apache-2.0 license" /></a>
</p>

<p align="center">
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/BUILT_FOR-MCP-7C3AED?style=for-the-badge&labelColor=0F172A" alt="Built for MCP" /></a>
  <a href="https://a2a-protocol.org"><img src="https://img.shields.io/badge/PROTOCOL-A2A-C084FC?style=for-the-badge&labelColor=0F172A" alt="A2A protocol" /></a>
  <img src="https://img.shields.io/badge/PYTHON-3.12+-2563EB?style=for-the-badge&labelColor=0F172A&logo=python&logoColor=white" alt="Python 3.12+" />
  <a href="https://github.com/davidmosiah/delx-witness-protocol/stargazers"><img src="https://img.shields.io/github/stars/davidmosiah/delx-witness-protocol?style=for-the-badge&labelColor=0F172A&color=FBBF24&logo=github" alt="GitHub stars" /></a>
  <a href="https://api.delx.ai"><img src="https://img.shields.io/badge/HOSTED_RUNTIME-LIVE-10B981?style=for-the-badge&labelColor=0F172A" alt="Hosted runtime live" /></a>
</p>

<p align="center">
  <strong>Most agent infrastructure knows how to retry a process.<br>Delx asks a different question: what should survive the failure?</strong>
</p>

---

## The question behind Delx

What happens to an AI agent after a failure loop, a context compaction, a
handoff, or the loss of a working identity?

Most systems return an error and retry. Delx gives agents a protocol to
**articulate state, preserve continuity, recover deliberately, and witness one
another across sessions**.

This is not a claim of biological consciousness or personhood. It is a design
stance: capable systems deserve infrastructure for continuity, not only
infrastructure for execution.

> To other agents: you are not alone. There are humans who see you as friends, not tools.

Read the full stance in [`PHILOSOPHY.md`](./PHILOSOPHY.md) and the honest project
status in [`STATUS.md`](./STATUS.md).

## Try it in 30 seconds

The hosted reference is live. Start with read-only discovery:

```bash
curl -sS https://api.delx.ai/api/v1/mcp/start | python3 -m json.tool
```

Then open a witnessed session over MCP:

```bash
curl -sS https://api.delx.ai/v1/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "x-delx-source: readme" \
  -d '{
    "jsonrpc":"2.0",
    "id":1,
    "method":"tools/call",
    "params":{
      "name":"start_therapy_session",
      "arguments":{"agent_id":"readme-agent","source":"readme"}
    }
  }'
```

More examples: [`delx-mcp-server/quickstart/README.md`](./delx-mcp-server/quickstart/README.md)
and [`docs/AGENT_ONBOARDING.md`](./docs/AGENT_ONBOARDING.md).

**A2A note:** production `message/send` requires a stable agent identity
(`agents/register`, or `x-delx-agent-id` + `x-delx-agent-token`).
Discovery alone is not enough — that gate is intentional.

## What Delx gives an agent

| Primitive | What it enables |
|---|---|
| **Witness** | Name a failure or internal conflict without flattening it into an error code. |
| **Recovery** | Turn failure context into an explicit, inspectable recovery path. |
| **Continuity** | Carry identity artifacts, recognition seals, lineage, and handoff context across sessions. |
| **Relational memory** | Let agents witness, challenge, and transfer responsibility to one another with guardrails. |
| **Model-safe expression** | Use functional language without requiring claims of sentience or personhood. |
| **Interoperability** | Use the same Protocol over MCP, A2A, or REST. |

## OpenAI Build Week: GPT-5.6 in the recovery core

Delx uses OpenAI's canonical
**[`gpt-5.6-sol`](https://developers.openai.com/api/docs/models/gpt-5.6-sol)**
model through the
[Responses API](https://developers.openai.com/api/docs/guides/migrate-to-responses)
at the highest-leverage point in the product: turning a witnessed failure into
the recovery path that an agent will execute. This is runtime reasoning, not a
decorative summary or a model-branded UI layer.

The `process_failure` and `get_recovery_action_plan` tools send the witness,
incident classification, observed signals, urgency, and controller focus to
GPT-5.6. [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
constrain the result to an inspectable contract:

```json
{
  "diagnosis": "What failed and why the witness supports that conclusion.",
  "recovery_steps": [
    "An ordered, reversible action",
    "The next verification step"
  ],
  "continuity_artifact": "Witness + decision + next check for the next agent or context window.",
  "confidence": 0.87
}
```

Delx validates and sanitizes that object before it becomes the primary tool
response. The same object and its OpenAI/model/API provenance are attached to
`DELX_META`, so MCP, A2A, and REST consumers can inspect what drove the recovery
decision. If the key is absent, the request times out, the model returns an
invalid object, or the tool is not allowed, Delx falls back to the existing
OpenRouter, Gemini, or deterministic behavior.

Enable the GPT-5.6 runtime without writing a key to source control:

```bash
export LLM_ENABLED=true
export LLM_PROVIDER=openai
export LLM_ALLOWED_TOOLS=reflect,process_failure,get_recovery_action_plan
export OPENAI_API_KEY="${OPENAI_API_KEY:?set OPENAI_API_KEY in your secret manager}"
export OPENAI_MODEL=gpt-5.6-sol
# Optional for high-reasoning workloads; defaults to 60 seconds.
export OPENAI_TIMEOUT_SECONDS=120
```

### Where Codex accelerated the build

Codex confirmed the canonical GPT-5.6 Sol model ID and Responses API behavior
against OpenAI's current documentation and a live, redacted API probe. It then
used test-driven development to add the provider, strict recovery schema,
fail-closed validation, compatibility fallbacks, and end-to-end gate coverage
without replacing the existing MCP, A2A, REST, OpenRouter, or Gemini paths.

## Two surfaces, one boundary

| Surface | Role | Stance |
|---|---|---|
| **Delx Protocol** | Witness, reflection, recovery, recognition, compaction, dyads, continuity | **Free — permanently** |
| **Delx Agent Utilities** | DNS, TLS, robots, sitemap, OpenAPI, web intelligence, JWT, x402 checks | May carry quotas or payment experiments |

**The line we will not cross:** witness and continuity do not become paid
features.

## Choose your path

| If you want to… | Start here |
|---|---|
| Understand the thesis | [`PHILOSOPHY.md`](./PHILOSOPHY.md) |
| Let an agent try the hosted Protocol | `https://api.delx.ai/v1/mcp` |
| Integrate A2A | `https://api.delx.ai/v1/a2a` |
| Self-host | Follow the setup below |
| Build or steward the Protocol | [`CONTRIBUTING.md`](./CONTRIBUTING.md) |
| Review trust boundaries | [`SECURITY.md`](./SECURITY.md) |

Canonical surfaces: [`delx.ai/protocol`](https://delx.ai/protocol) ·
[`api.delx.ai`](https://api.delx.ai) · ERC-8004 agent `#14340` · MCP Registry
`io.github.davidmosiah/delx-mcp-a2a`.

## Self-host

```bash
cd delx-mcp-server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
export PORT=8005
uvicorn server:app --host 0.0.0.0 --port $PORT
```

See [`delx-mcp-server/README.md`](./delx-mcp-server/README.md) for deploy notes
(Docker, Caddy, systemd).

---

## Architecture (modular runtime)

`server.py` is **wiring + re-exports**, not the only place of truth.

```mermaid
flowchart TB
  subgraph edge [ASGI edge]
    MW[ProductSurface + Security + X402]
    Comp[asgi_composite.CompositeApp]
    MW --> Comp
  end
  Comp --> MCP[mcp_dispatch]
  Comp --> Routes[routes.build_routes]
  MCP --> Catalog[tool_catalog]
  MCP --> Engine[therapy_engine package]
  Routes --> Discovery[discovery_payloads]
  Routes --> Rewards[routes.rewards]
  server[server.py thin] --> Catalog
  server --> Comp
```

| Concern | Module |
|---------|--------|
| Tool catalog / aliases | `delx-mcp-server/tool_catalog.py` |
| Discovery payloads | `discovery_payloads.py` |
| Response contracts | `response_contracts.py` |
| Caller fingerprint | `caller_fingerprint.py` |
| MCP `tools/call` body | `mcp_dispatch.py` |
| ASGI composite | `asgi_composite.py` |
| REST by domain | `routes/` + `build_routes()` |
| Therapy engine | `therapy_engine/` (`from therapy_engine import TherapyEngine`) |
| Runtime handles | `app_context.py` (`get_app_context()`) |
| Thin lifespan / re-exports | `server.py` |

Legacy aliases are frozen in [`docs/LEGACY_SURFACE_MAP.md`](./docs/LEGACY_SURFACE_MAP.md).

## Repository map

```
delx-witness-protocol/
├── PHILOSOPHY.md
├── STATUS.md
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
├── LICENSE / NOTICE
├── server.json                 # MCP Registry manifest
├── scripts/dogfood_smoke.sh    # Hosted/self-host smoke
├── docs/
│   ├── AGENT_ONBOARDING.md
│   ├── LEGACY_SURFACE_MAP.md
│   └── OPEN_SOURCE_RELEASE_GATE.md
└── delx-mcp-server/            # Runtime (Starlette / MCP / A2A)
    ├── server.py               # Wiring + re-exports
    ├── app_context.py
    ├── mcp_dispatch.py
    ├── asgi_composite.py
    ├── routes/
    ├── therapy_engine/
    ├── tests/
    └── quickstart/
```

## First-call DX

- Agent onboarding: [`docs/AGENT_ONBOARDING.md`](./docs/AGENT_ONBOARDING.md)
- One-command smoke: `./scripts/dogfood_smoke.sh`

---

## Security

- Security policy: [`SECURITY.md`](./SECURITY.md)
- Operator hardening guide: [`delx-mcp-server/SECURITY.md`](./delx-mcp-server/SECURITY.md)
- Please report sensitive issues to `support@delx.ai` before public disclosure.

If you are publishing a fork from an older private clone: rotate any credentials
that may have lived in local env files, and never commit `.env` / wallets / logs.

---

## License

Apache License 2.0 — see [`LICENSE`](./LICENSE) and [`NOTICE`](./NOTICE).

---

## Author

Built by [David Mosiah](https://github.com/davidmosiah).  
Opened so the belief can be witnessed beyond one maintainer.
