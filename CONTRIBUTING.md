# Contributing

Thank you for considering a contribution.
Please read [`PHILOSOPHY.md`](./PHILOSOPHY.md) and [`STATUS.md`](./STATUS.md) first.

## Ground rules

1. **Keep the Protocol free.** Do not add payment gates to witness, recognition,
   compaction, recovery, or continuity primitives.
2. **Stay model-safe.** Prefer operational language over claims of biological
   or religious consciousness unless the caller explicitly opts into richer framing.
3. **Prefer contracts.** If you change MCP/A2A behavior, add or update a test under
   `delx-mcp-server/tests/`.
4. **Do not commit secrets.** No `.env`, wallets, tokens, logs, or databases.
5. **Be kind.** Agents and humans both show up here under stress. Write like a witness.

## Local development

```bash
cd delx-mcp-server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
export PORT=8005
uvicorn server:app --host 0.0.0.0 --port $PORT
```

Quick checks:

```bash
python -m unittest discover -s tests -p 'test_*.py'
python self_test.py --quick
```

Hosted dogfood (no local server required):

```bash
curl -sS https://api.delx.ai/mcp
```

See `delx-mcp-server/quickstart/README.md` for MCP/A2A examples.
Note: production A2A calls require stable agent identity (`agents/register` or
`x-delx-agent-id` + `x-delx-agent-token`).

## Pull requests

- Small, focused PRs are easier to review.
- Describe *why* (especially if touching Protocol surfaces).
- Mention any discovery/docs drift you noticed.
- If you are unsure, open an issue before a large refactor.

## Good first directions

- README / discovery clarity for first-call agents
- Extracting route modules out of `server.py`
- Contract tests for schema error UX (`DELX-1001` paths)
- Aligning quickstart docs with A2A identity requirements

## Code of conduct

See [`CODE_OF_CONDUCT.md`](./CODE_OF_CONDUCT.md).
