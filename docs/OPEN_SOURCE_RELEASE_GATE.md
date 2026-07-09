# Open-source release gate

This checklist applies only to the history-clean public snapshot intended for:

- GitHub repository: `davidmosiah/delx-witness-protocol`
- MCP Registry name: `io.github.davidmosiah/delx-mcp-a2a`
- Release: `3.3.1`

The private operational repository `davidmosiah/delx-mcp-a2a` is a different
repository. Do not change its visibility, rewrite its history, or replace its
remote while publishing this snapshot.

## Local release gate

Run from the root of the clean snapshot:

```bash
git status --short --branch
git log --format='%h %an <%ae> %s' --all
git rev-list --objects --all | rg -i 'coinbase_env_tmp|\.env$|wallet\.json|\.log$|\.db$'
git ls-files | rg -i '(^|/)(\.env|.*\.log|.*\.db|wallet\.json)$' && exit 1 || true

gitleaks git --config .gitleaks.toml --redact=100 --no-banner .
gitleaks dir --config .gitleaks.toml --redact=100 --no-banner .
osv-scanner scan source -r .

cd delx-mcp-server
python3 -m venv .venv-release
source .venv-release/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt
pip check
ruff check .
mypy --config-file pyproject.toml \
  app_context.py tool_catalog.py discovery_payloads.py \
  response_contracts.py caller_fingerprint.py
python -m unittest discover -s tests -p 'test_*.py' -v
deactivate
rm -rf .venv-release
```

Then run the local HTTP smoke against a disposable database:

```bash
cd delx-mcp-server
DATABASE_PATH=/tmp/delx-oss-release.db LOG_LEVEL=WARNING \
  python -m uvicorn server:app --host 127.0.0.1 --port 8005

# In a second terminal:
python self_test.py --quick
```

Expected result: all checks pass, the self-test reports zero failures, and
`GET /api/v1/public-sessions?limit=1` returns HTTP 200.

## Repository and metadata checks

- [ ] `server.json`, `pyproject.toml`, runtime discovery, and agent card all say `3.3.1`.
- [ ] `server.json.repository.url` points to `davidmosiah/delx-witness-protocol`.
- [ ] `git log --format='%ae' --all` contains only the public maintainer email.
- [ ] No database, log, report, environment, wallet, or credential file is tracked.
- [ ] Gitleaks and OSV exit successfully using the committed configuration.
- [ ] Unit tests, full Ruff, Mypy, and local HTTP self-test are green.
- [ ] `SECURITY.md` accurately documents storage, outbound calls, and tenant boundaries.
- [ ] The private operational repository remains private and unchanged.

## Safe GitHub publication sequence

1. Create `davidmosiah/delx-witness-protocol` as a **new private repository**.
2. Push this clean history to that repository; never force-push the operational repo.
3. Enable secret scanning, Dependabot alerts, and branch protection.
4. Let the pinned GitHub Actions finish successfully on the private repository.
5. Re-run Gitleaks against all remote refs.
6. Review the rendered README, license, security policy, and repository metadata.
7. Obtain explicit human approval before changing visibility to public.
8. After publication, verify the public clone and publish MCP Registry version `3.3.1`.

## Automation policy

Automated agents may prepare and push the clean snapshot to the new private
repository when explicitly authorized. They must not:

- change visibility to public without explicit final approval;
- target `davidmosiah/delx-mcp-a2a` during OSS publication;
- run history-rewriting or force-push commands;
- print, commit, or copy live credentials and production data.
