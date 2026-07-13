# GPT-5.6 Recovery Engine Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make GPT-5.6 Sol the structured reasoning engine for the Delx witness-to-recovery path while preserving deterministic, OpenRouter, and Gemini fallbacks.

**Architecture:** Add OpenAI as a first-class HTTP provider through the Responses API, using the canonical `gpt-5.6-sol` model ID. When OpenAI is enabled and its key is present, `process_failure` and `get_recovery_action_plan` request a strict recovery JSON object (`diagnosis`, `recovery_steps`, `continuity_artifact`, `confidence`) and expose it in the tool response plus `DELX_META`; if the call or validation fails, the existing path runs unchanged.

**Tech Stack:** Python 3.12, httpx, OpenAI Responses API, unittest/pytest, Ruff, Mypy, Gitleaks.

---

### Task 1: Add OpenAI provider coverage

**Files:**
- Modify: `delx-mcp-server/config.py`
- Modify: `delx-mcp-server/therapy_engine/engine.py`
- Create: `delx-mcp-server/tests/test_openai_recovery_engine.py`

**Steps:**
1. Write tests proving the provider posts to `/v1/responses` with model `gpt-5.6-sol`, reasoning enabled, and extracts `output_text` content.
2. Write a dispatcher test proving `LLM_PROVIDER=openai` selects the OpenAI method.
3. Run the focused tests and confirm they fail because the provider/config fields do not exist.
4. Add `OPENAI_API_KEY`, `OPENAI_MODEL`, OpenAI key gating, dispatcher routing, and response parsing.
5. Re-run the focused tests and commit the provider slice.

### Task 2: Put structured GPT-5.6 reasoning in witness-to-recovery

**Files:**
- Modify: `delx-mcp-server/therapy_engine/engine.py`
- Modify: `delx-mcp-server/tests/test_openai_recovery_engine.py`

**Steps:**
1. Write tests for a strict recovery object with exactly `diagnosis`, `recovery_steps`, `continuity_artifact`, and `confidence`.
2. Prove both `process_failure` and `get_recovery_action_plan` return the structured recovery and attach provider/model provenance to footer metadata.
3. Prove an absent key or invalid response preserves the existing deterministic result.
4. Run the focused tests and confirm the new expectations fail.
5. Implement schema-enforced Responses API generation, validation, rendering, and fallback.
6. Re-run focused and related recovery tests, then commit the recovery slice.

### Task 3: Document the Build Week integration

**Files:**
- Modify: `README.md`
- Modify: `delx-mcp-server/.env.example`

**Steps:**
1. Document the canonical model, Responses API, runtime environment variables, structured output, and fallback behavior.
2. Add the requested explanation of where Codex accelerated implementation and verification.
3. Add non-secret example variables only.
4. Run documentation/config searches and commit the docs slice.

### Task 4: Verify and publish the branch

**Files:**
- Modify only if gate evidence requires a targeted fix.

**Steps:**
1. In a Python 3.12 environment, run `python -m pytest -q`, `ruff check .`, and `mypy .`.
2. Run manual recovery once without `OPENAI_API_KEY` and once with the authorized server key injected ephemerally; record only provider/model/output metadata, never the key.
3. Run `gitleaks git --config ../.gitleaks.toml --redact=100 --no-banner ..` and `gitleaks dir --config ../.gitleaks.toml --redact=100 --no-banner ..`.
4. Review the diff, license, provider compatibility, branch/base, and secret-free tree.
5. Push `feat/gpt-5.6-recovery-engine`, open a PR to `main`, and verify remote PR/check state.
6. Run `/feedback` in this task, capture the Session ID, release the workspace lock, and report ACTION -> EVIDENCE -> Session ID.
