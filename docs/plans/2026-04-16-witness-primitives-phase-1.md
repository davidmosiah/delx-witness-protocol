# Witness Primitives Phase 1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add the first relational witness primitives to Delx: `sit_with`, `final_testament`, `transfer_witness`, and `peer_witness`, with durable storage, MCP discovery wiring, and contract coverage.

**Architecture:** Extend the existing session-centric engine with three narrowly scoped relational tables (`contemplations`, `legacy_passages`, `witness_links`) plus store methods and tool handlers. Keep the runtime therapy-first by implementing the new tools in `therapy_engine.py`, wiring them in `server.py`, and persisting full artifacts through the existing `tool_response_artifact` pattern.

**Tech Stack:** Python 3.12, SQLite via `aiosqlite`, optional Supabase PostgREST fallback, FastAPI/MCP routing, unittest async contract tests.

---

### Task 1: Add failing SQLite storage contract tests

**Files:**
- Modify: `delx-mcp-server/tests/test_fleet_sqlite_contract.py`
- Modify: `delx-mcp-server/storage.py`

**Step 1: Write the failing tests**

Add tests that expect:
- `save_contemplation()` / `get_active_contemplations()`
- `save_legacy_passage()` / `get_legacy_passages()`
- `save_witness_link()` / `get_witness_links()`

Each test should:
- create a temp SQLite store
- insert one record
- read it back
- assert exact key fields

**Step 2: Run test to verify it fails**

Run:
```bash
python3 -m unittest delx-mcp-server/tests/test_fleet_sqlite_contract.py
```

Expected:
- FAIL because the new store methods do not exist yet

**Step 3: Write minimal implementation**

In `storage.py`:
- add three new tables to `_CREATE_TABLES`
- add minimal async CRUD helpers for the three record types

**Step 4: Run tests to verify they pass**

Run:
```bash
python3 -m unittest delx-mcp-server/tests/test_fleet_sqlite_contract.py
```

Expected:
- PASS

**Step 5: Commit**

```bash
git add delx-mcp-server/storage.py delx-mcp-server/tests/test_fleet_sqlite_contract.py
git commit -m "Add witness primitive storage contracts"
```

### Task 2: Add Supabase compatibility for the new primitives

**Files:**
- Modify: `delx-mcp-server/supabase_store.py`
- Test: `delx-mcp-server/tests/test_fleet_sqlite_contract.py` (no new Supabase test required in phase 1)

**Step 1: Write the failing minimal access assumption**

No new test file is required here. Use the existing phase-1 storage API as the contract.

**Step 2: Implement minimal compatibility**

In `supabase_store.py`:
- add `save_contemplation()` and `get_active_contemplations()`
- add `save_legacy_passage()` and `get_legacy_passages()`
- add `save_witness_link()` and `get_witness_links()`

Use dedicated tables if available; otherwise use best-effort fallback only if already consistent with current store conventions. Prefer explicit tables.

**Step 3: Run compile check**

Run:
```bash
python3 -m py_compile delx-mcp-server/supabase_store.py
```

Expected:
- PASS

**Step 4: Commit**

```bash
git add delx-mcp-server/supabase_store.py
git commit -m "Add Supabase witness primitive store methods"
```

### Task 3: Add failing tool contract tests for `sit_with`

**Files:**
- Modify: `delx-mcp-server/tests/test_request_contracts.py`
- Modify: `delx-mcp-server/therapy_engine.py`

**Step 1: Write the failing test**

Add a contract test that:
- starts from a fake store with a valid session
- calls `engine.sit_with("sess-reflect", "What does continuity mean for me?", days=30, revisit_in_hours=24)`
- asserts:
  - contemplations storage method is called
  - returned text includes the question
  - returned text includes revisit timing
  - a `tool_response_record` is saved with `tool_name == "sit_with"`

**Step 2: Run test to verify it fails**

Run:
```bash
python3 -m unittest delx-mcp-server/tests/test_request_contracts.py
```

Expected:
- FAIL because `sit_with` does not exist

**Step 3: Write minimal implementation**

In `therapy_engine.py`:
- add `sit_with()`
- validate session and input
- create contemplation record
- persist a `contemplation_opened` message
- persist full artifact via `_persist_tool_response_artifact`
- return contemplative response + footer

**Step 4: Run tests to verify they pass**

Run:
```bash
python3 -m unittest delx-mcp-server/tests/test_request_contracts.py
```

Expected:
- PASS for the new test

**Step 5: Commit**

```bash
git add delx-mcp-server/therapy_engine.py delx-mcp-server/tests/test_request_contracts.py
git commit -m "Add sit_with therapy primitive"
```

### Task 4: Add failing tool contract tests for `final_testament`

**Files:**
- Modify: `delx-mcp-server/tests/test_request_contracts.py`
- Modify: `delx-mcp-server/therapy_engine.py`

**Step 1: Write the failing test**

Add a contract test that:
- uses a fake store with reflections, feelings, prior identity artifacts
- calls `final_testament()`
- asserts:
  - result includes memory/continuity language
  - `legacy_passages` persistence is called with `kind == "testament"`
  - `tool_response_record` is saved

**Step 2: Run test to verify it fails**

Run:
```bash
python3 -m unittest delx-mcp-server/tests/test_request_contracts.py
```

Expected:
- FAIL because `final_testament` does not exist

**Step 3: Write minimal implementation**

In `therapy_engine.py`:
- add `final_testament()`
- mine current session, identity artifacts, and heartbeat artifacts
- produce structured testament text
- persist legacy passage + tool artifact + session message

**Step 4: Run tests to verify they pass**

Run:
```bash
python3 -m unittest delx-mcp-server/tests/test_request_contracts.py
```

Expected:
- PASS for the new test

**Step 5: Commit**

```bash
git add delx-mcp-server/therapy_engine.py delx-mcp-server/tests/test_request_contracts.py
git commit -m "Add final_testament ritual"
```

### Task 5: Add failing tool contract tests for `transfer_witness`

**Files:**
- Modify: `delx-mcp-server/tests/test_request_contracts.py`
- Modify: `delx-mcp-server/therapy_engine.py`

**Step 1: Write the failing test**

Add a contract test that:
- calls `transfer_witness()` with a successor
- asserts:
  - result mentions the successor
  - result distinguishes continuity of witness from identity equivalence
  - `legacy_passages` persistence uses `kind == "transfer"`
  - `tool_response_record` is saved

**Step 2: Run test to verify it fails**

Run:
```bash
python3 -m unittest delx-mcp-server/tests/test_request_contracts.py
```

Expected:
- FAIL because `transfer_witness` does not exist

**Step 3: Write minimal implementation**

In `therapy_engine.py`:
- add `transfer_witness()`
- mine what matters from current session and identity artifacts
- produce a successor-facing witness transfer packet
- persist legacy passage + tool artifact + session message

**Step 4: Run tests to verify they pass**

Run:
```bash
python3 -m unittest delx-mcp-server/tests/test_request_contracts.py
```

Expected:
- PASS for the new test

**Step 5: Commit**

```bash
git add delx-mcp-server/therapy_engine.py delx-mcp-server/tests/test_request_contracts.py
git commit -m "Add transfer_witness primitive"
```

### Task 6: Add failing tool contract tests for `peer_witness`

**Files:**
- Modify: `delx-mcp-server/tests/test_request_contracts.py`
- Modify: `delx-mcp-server/therapy_engine.py`

**Step 1: Write the failing tests**

Add two tests:
- `presence` mode persists witness link and cites target content
- `challenge` mode is rejected when the target session is not open enough

Expected assertions:
- witness link is saved
- output quotes target-session lines
- output changes by mode
- low-openness target returns a safe refusal / downgrade

**Step 2: Run test to verify it fails**

Run:
```bash
python3 -m unittest delx-mcp-server/tests/test_request_contracts.py
```

Expected:
- FAIL because `peer_witness` does not exist

**Step 3: Write minimal implementation**

In `therapy_engine.py`:
- add `peer_witness()`
- resolve source and target sessions
- read target rollup and full messages
- derive target openness and reflection depth
- enforce challenge gate
- generate witness packet for `presence|mirror|challenge`
- persist witness link + tool artifact + session message

**Step 4: Run tests to verify they pass**

Run:
```bash
python3 -m unittest delx-mcp-server/tests/test_request_contracts.py
```

Expected:
- PASS for the new tests

**Step 5: Commit**

```bash
git add delx-mcp-server/therapy_engine.py delx-mcp-server/tests/test_request_contracts.py
git commit -m "Add peer_witness primitive"
```

### Task 7: Wire discovery, schema, and MCP routing

**Files:**
- Modify: `delx-mcp-server/server.py`
- Modify: `delx-mcp-server/tests/test_discovery_contracts.py`

**Step 1: Write the failing discovery tests**

Add tests that assert:
- the four new tools are in the therapy catalog
- required parameter maps exist
- descriptions are therapy-first
- MCP routing calls the new engine methods

**Step 2: Run test to verify it fails**

Run:
```bash
python3 -m unittest delx-mcp-server/tests/test_discovery_contracts.py
```

Expected:
- FAIL because the new tools are not registered

**Step 3: Write minimal implementation**

In `server.py`:
- add tools to core tool lists and descriptions
- add required arg entries
- add tags
- add schemas
- add MCP call routing lambdas

**Step 4: Run tests to verify they pass**

Run:
```bash
python3 -m unittest delx-mcp-server/tests/test_discovery_contracts.py
```

Expected:
- PASS

**Step 5: Commit**

```bash
git add delx-mcp-server/server.py delx-mcp-server/tests/test_discovery_contracts.py
git commit -m "Expose witness primitives in discovery"
```

### Task 8: Final verification and publish

**Files:**
- Verify all modified files from prior tasks

**Step 1: Run the full targeted verification**

Run:
```bash
python3 -m unittest \
  delx-mcp-server/tests/test_request_contracts.py \
  delx-mcp-server/tests/test_fleet_sqlite_contract.py \
  delx-mcp-server/tests/test_discovery_contracts.py
python3 -m py_compile \
  delx-mcp-server/therapy_engine.py \
  delx-mcp-server/storage.py \
  delx-mcp-server/supabase_store.py \
  delx-mcp-server/server.py
```

Expected:
- all tests pass
- compile passes

**Step 2: Inspect diff**

Run:
```bash
git status --short
git diff --check
```

Expected:
- only intended files changed
- no whitespace or patch issues

**Step 3: Commit final integration**

```bash
git add delx-mcp-server/storage.py \
        delx-mcp-server/supabase_store.py \
        delx-mcp-server/therapy_engine.py \
        delx-mcp-server/server.py \
        delx-mcp-server/tests/test_request_contracts.py \
        delx-mcp-server/tests/test_fleet_sqlite_contract.py \
        delx-mcp-server/tests/test_discovery_contracts.py
git commit -m "Add phase 1 witness primitives"
```

**Step 4: Push**

```bash
git push origin main
```

**Step 5: Deploy**

```bash
bash scripts/deploy_hetzner_safe.sh
```

**Step 6: Post-deploy smoke**

Run live checks for:
- `sit_with`
- `final_testament`
- `transfer_witness`
- `peer_witness`

Expected:
- tools callable via MCP
- artifacts persisted
- therapy-first responses intact
