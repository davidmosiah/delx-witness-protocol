# Open-source release gate

This file is for the maintainer. Do not skip the security steps.

## Modularization + SOTA polish — DONE locally

- Phases 0–5 modularization committed (`f815dad` and follow-ups)
- CI unittest + ruff/mypy on thin modules
- `docs/LEGACY_SURFACE_MAP.md` with removal criteria
- `docs/AGENT_ONBOARDING.md` + `scripts/dogfood_smoke.sh`
- `app_context.py` for explicit runtime handles
- README architecture map

## CRITICAL — before public visibility

A Coinbase CDP credential file (`.coinbase_env_tmp.sh`) was previously committed
in commit `8b6b2fd`. It has been removed from the **index** and gitignored, but
it still exists in **git history** until history is rewritten **and/or** the key
is fully rotated and treated as burned.

Local backup (not in repo): `/tmp/delx-ROTATE-CDP-KEYS-20260708.sh`

### Checklist (human — do in order)

- [ ] **1. Rotate / revoke** the CDP API key in the Coinbase developer dashboard
      (treat as compromised even while the repo is private).
- [ ] **2. Confirm rotation** — old key rejected; new key only in secret store / host env.
- [ ] **3. Purge history while still private** (preferred):

```bash
# Install once: brew install git-filter-repo
cd /path/to/delx-mcp-a2a
git filter-repo --invert-paths --path .coinbase_env_tmp.sh --force
# Re-add origin if filter-repo removed it:
# git remote add origin git@github.com:davidmosiah/delx-mcp-a2a.git
git push --force origin main   # PRIVATE repo only
```

- [ ] **4. Re-scan:**

```bash
git log -p --all -S 'CDP_API_KEY' | head
git log -p --all -S 'coinbase_env' | head
git ls-files | rg -i 'env$|secret|token|wallet|\.log$'
```

- [ ] **5. Push modularization commits** to private `main` (after purge, or before
      if you chose rotate-only minimum).
- [ ] **6. Only then:** `gh repo edit davidmosiah/delx-mcp-a2a --visibility public`
- [ ] **7. Publish** adapted text from `docs/OPEN_SOURCE_ANNOUNCEMENT.md`

**Do not flip visibility while CDP material remains in history unrotated.**

### Agent / automation policy

Automated agents in this workspace must **not**:

- run `gh repo edit --visibility public`
- run `git filter-repo` / force-push
- commit or paste live CDP secrets

until the human checklist items 1–2 are explicitly confirmed in chat.

## Included in OSS prep

- `LICENSE` (Apache-2.0), `NOTICE`, `README.md`, `PHILOSOPHY.md`, `STATUS.md`
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`
- `docs/OPEN_SOURCE_ANNOUNCEMENT.md`, `docs/LEGACY_SURFACE_MAP.md`,
  `docs/AGENT_ONBOARDING.md`
- `.github/workflows/unit-tests.yml`, `delx-mcp-server/pyproject.toml`
- `.coinbase_env_tmp.sh` removed from tracking
