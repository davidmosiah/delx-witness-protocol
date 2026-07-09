# Security policy

## Supported version

Security fixes target the latest `3.3.x` release on the default branch. Older
snapshots and private forks may not receive backports.

## Reporting a vulnerability

Email `support@delx.ai` with:

- affected endpoint or component;
- reproduction steps and impact;
- whether production data or credentials may be involved;
- a safe way to contact you.

Do not open a public issue for an unpatched vulnerability and do not include
live tokens, session contents, private prompts, wallet keys, or third-party PII
in a report. This is a best-effort maintained project; receipt and remediation
times are not guaranteed.

## Trust boundaries

Delx is a public reference runtime and a self-hostable server. It is **not a
general-purpose tenant-isolation boundary**. Operators and callers must assume:

- Protocol sessions and events are persisted in SQLite or Supabase unless the
  deployment is configured otherwise.
- Only sessions explicitly opted into the public feed should be exposed by the
  consent-gated public-session endpoint.
- Stable A2A/stateful flows use an agent ID and token; callers must protect the
  token as a credential.
- LLM providers, Sentry, Supabase, payment facilitators, and URL/DNS/TLS utility
  targets are outbound systems when their features are enabled.
- Stateless web utilities process caller-supplied targets and must retain their
  SSRF, private-network, redirect, timeout, and response-size protections.
- The hosted reference is not appropriate for secrets or sensitive third-party
  data. Self-host for stricter privacy or isolation requirements.

## Secret and data handling

- Never commit `.env`, databases, logs, reports, wallet files, private keys, or
  production exports.
- Keep service-role keys and facilitator credentials in the host secret store.
- Rotate any credential that appears in a commit, build log, issue, or chat.
- Use separate credentials and databases for development, CI, and production.
- Review retention, logging, Sentry sampling, and public-session consent before
  accepting real traffic.

The maintainer release checklist is in
[`docs/OPEN_SOURCE_RELEASE_GATE.md`](./docs/OPEN_SOURCE_RELEASE_GATE.md). The
deployment hardening guide is in
[`delx-mcp-server/SECURITY.md`](./delx-mcp-server/SECURITY.md).
