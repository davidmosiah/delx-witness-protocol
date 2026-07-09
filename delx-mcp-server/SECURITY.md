# Delx deployment hardening

Read the repository-level [`SECURITY.md`](../SECURITY.md) first. This guide is
for operators who self-host the runtime.

## Before deployment

- Create a dedicated unprivileged service account.
- Copy `.env.example` to a host-managed secret location; never commit `.env`.
- Set restrictive permissions on secret files (`chmod 600`).
- Use a fresh empty database or a dedicated Supabase project.
- Configure HTTPS at the reverse proxy and expose only required ports.
- Keep admin PINs, service-role keys, LLM keys, payment credentials, and wallet
  material outside the repository and container image.
- Decide whether LLM, Sentry, Supabase, artwork upload, utilities, and payment
  integrations are required; leave unused integrations disabled.

## Authentication and isolation

Public discovery and free Protocol entrypoints are intentionally reachable.
Stateful A2A flows use an `agent_id` plus issued agent token. This is not a
general multi-tenant authorization system: do not place mutually untrusted
customers on one deployment without adding an application-specific identity,
authorization, and data-isolation layer.

Utility API keys and agent credentials are stored as hashes. Treat plaintext
tokens returned at creation time as secrets; the server cannot recover them.

## Outbound network behavior

Depending on configuration and selected tools, the server can call:

- caller-selected HTTP/DNS/TLS/OpenAPI targets;
- configured LLM providers;
- Supabase and Sentry;
- x402/MPP payment facilitators;
- object storage used by artwork flows.

Do not describe the runtime as offline or read-only. Session, event, feedback,
artwork, API-key, and payment-audit flows can write state. Preserve the existing
private-network/metadata-address blocking, redirect limits, timeouts, response
limits, and output sanitization when changing utility code.

## Production checklist

1. Run the complete open-source release gate.
2. Pin the container/image and Python dependency versions used in production.
3. Terminate TLS at Caddy or another maintained reverse proxy.
4. Run the service as an unprivileged user with a read-only application tree and
   writable state directory only.
5. Restrict firewall ingress to SSH from trusted sources plus HTTP/HTTPS.
6. Back up the database and test restoration without copying secrets into git.
7. Configure log retention and ensure payloads, authorization headers, and
   credentials are not logged.
8. Monitor error rate, storage growth, rate-limit events, and dependency alerts.

## Incident response

1. Isolate the affected deployment.
2. Preserve relevant logs without posting them publicly.
3. Rotate exposed credentials and agent/admin tokens.
4. Review database access, outbound calls, and deployment history.
5. Restore from a known-good release and verified backup.
6. Report repository vulnerabilities privately to `support@delx.ai`.
