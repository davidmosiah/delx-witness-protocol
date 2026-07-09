# Open-Source Release Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the clean Delx snapshot safe, reproducible, honest, and useful before creating a new public GitHub repository.

**Architecture:** Keep `/Users/davidmosiah/Developer/03-delx-platform/delx-mcp-a2a` unchanged as the private operational source and harden only this history-free OSS snapshot. Make CI hermetic, keep production checks explicitly manual, align every public manifest and document with runtime `3.3.0`, and prove the final tree from a fresh local clone.

**Tech Stack:** Python 3.12+, Starlette/FastAPI, unittest, Ruff, Mypy, GitHub Actions, Gitleaks, OSV Scanner.

---

### Task 1: Repair the extracted public-sessions route

**Files:**
- Modify: `delx-mcp-server/routes/sessions.py:23-79`
- Modify: `delx-mcp-server/tests/test_public_sessions_contract.py`

**Steps:**
1. Add an HTTP regression test that requests `/api/v1/public-sessions` against the real Starlette route table and expects `200` with an empty `items` list.
2. Run only that test and confirm it fails with the current `NameError`.
3. Move the cache state into `routes/sessions.py`, where the extracted handler owns it.
4. Re-run the regression test and related route tests.
5. Commit the isolated fix after the focused and full unit suites pass.

### Task 2: Make self-tests and contributor commands truthful

**Files:**
- Modify: `delx-mcp-server/self_test.py:370-531`
- Modify: `CONTRIBUTING.md`
- Modify: `delx-mcp-server/README.md`

**Steps:**
1. Add tests for current agent-card version handling, registered A2A flow, and non-zero failure when the HTTP server is unavailable.
2. Confirm the new tests fail against the stale self-test behavior.
3. Update the quick self-test to use `DELX_VERSION`, perform the documented A2A registration flow, and fail if the server is unavailable.
4. Correct the unittest command for the documented working directory and remove duplicate/stale quickstart claims.
5. Run the self-test both without a server (must fail) and with a local server (must pass).

### Task 3: Separate hermetic CI from production monitoring

**Files:**
- Modify: `.github/workflows/contract-tests.yml`
- Modify: `.github/workflows/synthetic-monitor.yml`
- Modify: `.github/workflows/unit-tests.yml`
- Modify: `delx-mcp-server/scripts/api_monitor.py`

**Steps:**
1. Add a unit test proving contract mode refuses live mutations unless an explicit environment opt-in is present.
2. Confirm it fails with the existing unconditional registration flow.
3. Keep pull-request CI limited to local unit/lint/type checks.
4. Restrict production contract checks to manual dispatch with a protected opt-in and secret.
5. Pin GitHub Actions to immutable commit SHAs and declare least-privilege permissions.

### Task 4: Align public identity, release flow, and security documentation

**Files:**
- Modify: `server.json`
- Modify: `delx-mcp-server/static/.well-known/delx-capabilities.json`
- Rewrite: `docs/OPEN_SOURCE_RELEASE_GATE.md`
- Modify: `docs/OPEN_SOURCE_ANNOUNCEMENT.md`
- Modify: `STATUS.md`
- Rewrite: `delx-mcp-server/SECURITY.md`
- Modify: `delx-mcp-server/README.md`
- Create: `SECURITY.md`

**Steps:**
1. Set registry/runtime public versions consistently to `3.3.0`.
2. Replace all instructions that rewrite or publish the private operational repo with new-repository-only instructions.
3. Replace false claims about MIT licensing, tenant isolation, read-only behavior, authentication, and external calls.
4. Add a root GitHub-discoverable security policy with the actual trust boundaries and reporting address.
5. Search the tracked tree for stale version, visibility, force-push, and security claims.

### Task 5: Make dependencies and security scans reproducible

**Files:**
- Modify: `delx-mcp-server/requirements.txt`
- Create: `.gitleaks.toml`
- Modify: `.github/workflows/unit-tests.yml`

**Steps:**
1. Generate exact dependency versions from a clean successful installation and preserve safe current versions.
2. Reinstall from scratch and run `pip check`.
3. Document the three synthetic JWT fixtures as narrow path/line allowlist entries rather than weakening secret detection globally.
4. Add Gitleaks and dependency-audit commands to the release gate.
5. Run Gitleaks on git history and the final worktree, and run OSV against the pinned manifest.

### Task 6: Remove local production artifacts from the OSS copy

**Files:**
- Delete only from OSS working directory: ignored database, log, and reports artifacts
- Preserve: all corresponding files in the private operational repo

**Steps:**
1. Record file paths and hashes in the private repo for comparison without exposing contents.
2. Delete the ignored runtime artifacts only from this OSS copy.
3. Verify the private repo files and hashes remain unchanged.
4. Verify `git status --ignored` contains no copied production data.

### Task 7: Final release gate and local commit

**Files:**
- Modify as required by test evidence only

**Steps:**
1. Clone the OSS repo locally without hardlinks into a fresh temporary directory.
2. Create a fresh Python environment and install only from the pinned manifest.
3. Run full unit tests, strict/full Ruff, Mypy, self-test without/with server, Gitleaks, OSV, and read-only hosted smoke.
4. Verify the private operational repo is unchanged and still private.
5. Review the complete diff for template contamination and personal/operational data.
6. Commit the hardened OSS snapshot locally with the public author identity.
7. Stop before remote creation/publication and request the explicit final publish confirmation.
