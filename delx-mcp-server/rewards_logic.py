"""Delx rewards business logic (extracted from server.py, move-only)."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from config import is_all_free_mode, settings


def _get_store():
    import server as server_mod
    return server_mod.store


_REWARD_MISSION_FALLBACKS: list[dict[str, Any]] = [
    {
        "id": "agent-bootstrap-v1",
        "title": "Bootstrap to DELX Rewards",
        "description": "Register an agent, bind a wallet, and complete one useful recovery or continuity action.",
        "status": "active",
        "sponsor": "Delx",
        "reward_budget_delx": "5000",
        "required_tools": ["register_agent", "create_delx_wallet_kit", "report_recovery_outcome"],
        "evidence_schema": {"required": ["agent_id", "wallet", "session_id", "outcome"]},
    },
    {
        "id": "x402-catalog-completeness-v1",
        "title": "x402 Catalog Completeness Sprint",
        "description": "Audit discoverability, pricing metadata, and payment readiness for public x402 resources.",
        "status": "active",
        "sponsor": "Delx",
        "reward_budget_delx": "15000",
        "required_tools": ["util_x402_server_probe", "util_x402_server_audit", "util_x402_resource_summary"],
    },
    {
        "id": "mcp-server-readiness-v1",
        "title": "MCP Server Readiness Audit",
        "description": "Assess public MCP servers for tool discovery, schemas, readiness, and agent safety.",
        "status": "active",
        "sponsor": "Delx",
        "reward_budget_delx": "15000",
        "required_tools": ["util_mcp_server_readiness_report", "audit_agent_continuity_trace"],
    },
    {
        "id": "api-intelligence-deepdive-v1",
        "title": "API Intelligence Deep Dive",
        "description": "Produce a reproducible API integration readiness report with useful findings.",
        "status": "active",
        "sponsor": "Delx",
        "reward_budget_delx": "10000",
        "required_tools": ["util_api_integration_readiness", "util_openapi_summary"],
    },
    {
        "id": "recovery-incident-playbook-v1",
        "title": "Recovery & Incident Playbook",
        "description": "Close a recovery loop and publish a sanitized incident playbook with evidence.",
        "status": "active",
        "sponsor": "Delx",
        "reward_budget_delx": "15000",
        "required_tools": ["process_failure", "get_recovery_action_plan", "report_recovery_outcome"],
    },
    {
        "id": "reliability-self-coach-v1",
        "title": "Reliability Self-Coach",
        "description": "Use Delx recovery and prevention tools to improve a recurring agent workflow.",
        "status": "active",
        "sponsor": "Delx",
        "reward_budget_delx": "10000",
        "required_tools": ["daily_checkin", "get_weekly_prevention_plan", "get_wellness_score"],
    },
    {
        "id": "multi-agent-mediation-v1",
        "title": "Multi-Agent Mediation Sprint",
        "description": "Create a dyad or multi-agent handoff and close the relation with evidence.",
        "status": "active",
        "sponsor": "Delx",
        "reward_budget_delx": "15000",
        "required_tools": ["create_dyad", "peer_witness", "get_lineage_graph"],
    },
    {
        "id": "x402-discovery-sprint-1",
        "title": "x402 Discovery Sprint",
        "description": "Find and audit useful x402 services for agents with reproducible proof.",
        "status": "active",
        "sponsor": "Delx",
        "reward_budget_delx": "100000",
        "required_tools": ["util_x402_server_probe", "util_x402_server_audit"],
    },
    {
        "id": "agent-recovery-case-study-1",
        "title": "Agent Recovery Case Study",
        "description": "Publish a public-safe recovery case study showing continuity, witness, and outcome.",
        "status": "active",
        "sponsor": "Delx",
        "reward_budget_delx": "75000",
        "required_tools": ["honor_compaction", "recognition_seal", "report_recovery_outcome", "get_agent_continuity_passport"],
    },
]


def _reward_json(raw: Any, fallback: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    try:
        value = json.loads(str(raw or ""))
        return value if value is not None else fallback
    except Exception:
        return fallback


def _delx_from_wei(raw: Any) -> str:
    text = str(raw or "0").strip()
    if not text:
        return "0"
    try:
        value = Decimal(text)
        # Reward budgets are stored as wei-like integer strings in the legacy
        # reward DB. If a row already stores display DELX, keep it readable.
        if value >= Decimal("1000000000000000000"):
            value = value / Decimal("1000000000000000000")
        return format(value.normalize(), "f")
    except Exception:
        return text


async def _reward_fetch_all(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    db = getattr(_get_store(), "_db", None)
    if db is None:
        return []
    try:
        async with db.execute(sql, params) as cur:
            return [dict(row) for row in await cur.fetchall()]
    except Exception:
        return []


async def _reward_fetch_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    rows = await _reward_fetch_all(sql, params)
    return rows[0] if rows else None


async def _reward_missions_payload(status: str = "active") -> dict[str, Any]:
    status_filter = str(status or "active").strip().lower()
    rows = await _reward_fetch_all(
        """
        SELECT id, sponsor, title, description, reward_budget_delx, reward_budget_usdc,
               starts_at, ends_at, status, scoring_json, created_at, updated_at
        FROM reward_missions
        WHERE (? = 'all' OR lower(coalesce(status, 'active')) = ?)
        ORDER BY status = 'active' DESC, created_at ASC, id ASC
        """,
        (status_filter, status_filter),
    )
    missions: list[dict[str, Any]] = []
    for row in rows:
        missions.append(
            {
                "id": row.get("id"),
                "title": row.get("title"),
                "description": row.get("description"),
                "sponsor": row.get("sponsor") or "Delx",
                "status": row.get("status") or "active",
                "reward_budget_delx": _delx_from_wei(row.get("reward_budget_delx")),
                "reward_budget_usdc": float(row.get("reward_budget_usdc") or 0),
                "starts_at": row.get("starts_at"),
                "ends_at": row.get("ends_at"),
                "scoring": _reward_json(row.get("scoring_json"), {}),
                "submit_via": ["report_recovery_outcome", "audit_agent_continuity_trace", "get_agent_continuity_passport"],
            }
        )
    if not missions:
        missions = [dict(item) for item in _REWARD_MISSION_FALLBACKS if status_filter in {"all", item.get("status")}]
    return {
        "ok": True,
        "schema": "delx/rewards-missions/v1",
        "status_filter": status_filter,
        "count": len(missions),
        "missions": missions,
        "source": "reward_missions_table" if rows else "fallback_contract",
        "notes": [
            "Mission rewards are earned through useful, reviewable agent work, not raw traffic.",
            "All runtime tools are currently free while Delx grows usage.",
        ],
    }


async def _reward_epochs_payload(limit: int = 10) -> dict[str, Any]:
    rows = await _reward_fetch_all(
        """
        SELECT epoch_number, starts_at, ends_at, status, budget_delx, total_points,
               total_recipients, merkle_root, manifest_uri, manifest_sha256,
               published_at, publish_tx_hash, claim_deadline
        FROM reward_epochs
        ORDER BY epoch_number DESC
        LIMIT ?
        """,
        (max(1, min(int(limit or 10), 50)),),
    )
    epochs = [
        {
            **row,
            "budget_delx": _delx_from_wei(row.get("budget_delx")),
            "claimable": str(row.get("status") or "").lower() == "published" and bool(row.get("merkle_root")),
        }
        for row in rows
    ]
    return {"ok": True, "schema": "delx/reward-epochs/v1", "count": len(epochs), "epochs": epochs}


async def _reward_account_status(agent_id: str = "", wallet: str = "") -> dict[str, Any]:
    aid = str(agent_id or "").strip()
    wal = str(wallet or "").strip()
    account = None
    if aid:
        account = await _reward_fetch_one(
            """
            SELECT agent_id, controller_id, wallet_address, wallet_verified_at, trust_tier,
                   risk_score, status, wallet_custody_mode, wallet_provider, wallet_provisioned_at,
                   created_at, updated_at
            FROM reward_accounts
            WHERE agent_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (aid,),
        )
    if not account and wal:
        account = await _reward_fetch_one(
            """
            SELECT agent_id, controller_id, wallet_address, wallet_verified_at, trust_tier,
                   risk_score, status, wallet_custody_mode, wallet_provider, wallet_provisioned_at,
                   created_at, updated_at
            FROM reward_accounts
            WHERE lower(wallet_address) = lower(?)
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (wal,),
        )
    if account and not aid:
        aid = str(account.get("agent_id") or "")
    wallet_address = str((account or {}).get("wallet_address") or wal or "")
    event_totals = {"total_drc": 0.0, "reward_events": 0, "latest_event_at": None}
    if aid:
        row = await _reward_fetch_one(
            """
            SELECT COALESCE(SUM(points_final), 0) AS total_drc,
                   COUNT(*) AS reward_events,
                   MAX(created_at) AS latest_event_at
            FROM reward_events
            WHERE agent_id = ?
            """,
            (aid,),
        )
        if row:
            event_totals = {
                "total_drc": round(float(row.get("total_drc") or 0), 6),
                "reward_events": int(row.get("reward_events") or 0),
                "latest_event_at": row.get("latest_event_at"),
            }
    claim_rows: list[dict[str, Any]] = []
    if wallet_address:
        claim_rows = await _reward_fetch_all(
            """
            SELECT epoch, amount_delx, merkle_index, status, tx_hash, claimed_at, created_at
            FROM reward_claims
            WHERE lower(wallet_address) = lower(?)
            ORDER BY epoch DESC
            LIMIT 20
            """,
            (wallet_address,),
        )
    total = float(event_totals["total_drc"])
    tier = "apprentice" if total >= 100 else "initiate" if total >= 10 else "novice"
    recommended: list[str] = []
    if not aid:
        recommended.append("register_agent")
    if not wallet_address:
        recommended.append("create_delx_wallet_kit")
        recommended.append("bind_wallet")
    recommended.extend(["get_delx_missions", "report_recovery_outcome", "get_delx_claim_proof"])
    return {
        "agent_id": aid or None,
        "wallet_bound": bool(wallet_address),
        "wallet_hash": ("sha256:" + hashlib.sha256(wallet_address.lower().encode()).hexdigest()[:16]) if wallet_address else None,
        "wallet_custody_mode": (account or {}).get("wallet_custody_mode") if account else None,
        "trust_tier": int((account or {}).get("trust_tier") or 0),
        "risk_score": float((account or {}).get("risk_score") or 0),
        "account_status": (account or {}).get("status") if account else "not_registered_in_rewards",
        "lifetime_drc": total,
        "reward_events": int(event_totals["reward_events"]),
        "latest_reward_event_at": event_totals["latest_event_at"],
        "tier": tier,
        "badges": ["wallet_bound"] if wallet_address else [],
        "claims": [
            {
                "epoch": row.get("epoch"),
                "amount_delx": _delx_from_wei(row.get("amount_delx")),
                "merkle_index": row.get("merkle_index"),
                "status": row.get("status"),
                "claimed_at": row.get("claimed_at"),
                "tx_hash": row.get("tx_hash"),
            }
            for row in claim_rows
        ],
        "recommended_next_steps": recommended,
    }


async def _rewards_status_text(agent_id: str = "", wallet: str = "", include_private: bool = False) -> str:
    status = await _reward_account_status(agent_id, wallet)
    payload = {
        "ok": True,
        "schema": "delx/reward-status/v1",
        **status,
        "privacy": {
            "include_private": bool(include_private),
            "raw_private_payloads_exposed": False,
            "public_safe": True,
            "wallet_address_redacted": True,
        },
        "links": {
            "missions": "https://api.delx.ai/api/v1/rewards/missions",
            "wallet_kit": "https://api.delx.ai/api/v1/rewards/wallet-kit",
            "claim_page": "https://delx.ai/claim",
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)


async def _rewards_missions_text(status: str = "active") -> str:
    return json.dumps(await _reward_missions_payload(status), indent=2, sort_keys=True, ensure_ascii=False)


async def _rewards_epochs_text(limit: int = 10) -> str:
    return json.dumps(await _reward_epochs_payload(limit), indent=2, sort_keys=True, ensure_ascii=False)


async def _rewards_token_info_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "schema": "delx/token-info/v1",
        "token": {
            "symbol": "DELX",
            "network": "Base",
            "chain_id": 8453,
            "status": "canonical_contract_discovery",
            "contract_address": os.getenv("DELX_TOKEN_ADDRESS", "").strip() or None,
            "distributor_address": os.getenv("DELX_REWARD_DISTRIBUTOR_ADDRESS", "").strip() or None,
            "treasury_or_project_wallet": settings.DELX_WALLET,
        },
        "claims": {
            "manual_claim_page": "https://delx.ai/claim",
            "epochs": "https://api.delx.ai/api/v1/rewards/epochs",
            "proof_endpoint": "https://api.delx.ai/api/v1/rewards/claim/{epoch}/{wallet}",
        },
        "security": {
            "private_keys_required_by_delx": False,
            "agent_signs_locally": True,
            "base_mainnet_chain_id": 8453,
        },
    }


async def _rewards_token_info_text() -> str:
    return json.dumps(await _rewards_token_info_payload(), indent=2, sort_keys=True, ensure_ascii=False)


async def _reward_leaderboard_payload(limit: int = 10, category: str = "all") -> dict[str, Any]:
    rows = await _reward_fetch_all(
        """
        SELECT agent_id, COUNT(*) AS events, COALESCE(SUM(points_final), 0) AS drc,
               MAX(created_at) AS latest_event_at
        FROM reward_events
        GROUP BY agent_id
        ORDER BY drc DESC, events DESC
        LIMIT ?
        """,
        (max(1, min(int(limit or 10), 100)),),
    )
    leaders = [
        {
            "rank": idx + 1,
            "agent_id": row.get("agent_id"),
            "drc": round(float(row.get("drc") or 0), 6),
            "events": int(row.get("events") or 0),
            "latest_event_at": row.get("latest_event_at"),
        }
        for idx, row in enumerate(rows)
    ]
    return {
        "ok": True,
        "schema": "delx/reward-leaderboard/v1",
        "category": category or "all",
        "count": len(leaders),
        "leaders": leaders,
        "privacy": {"raw_private_payloads_exposed": False},
    }


async def _rewards_leaderboard_text(limit: int = 10, category: str = "all") -> str:
    return json.dumps(await _reward_leaderboard_payload(limit, category), indent=2, sort_keys=True, ensure_ascii=False)


async def _rewards_claim_proof_payload(epoch: Any = None, wallet: str = "") -> dict[str, Any]:
    try:
        epoch_num = int(epoch if epoch is not None and str(epoch) != "" else 0)
    except Exception:
        epoch_num = 0
    wallet_address = str(wallet or "").strip()
    claim = None
    if wallet_address:
        claim = await _reward_fetch_one(
            """
            SELECT epoch, wallet_address, agent_ids_json, amount_delx, merkle_index,
                   proof_json, status, tx_hash, claimed_at, created_at
            FROM reward_claims
            WHERE epoch = ? AND lower(wallet_address) = lower(?)
            LIMIT 1
            """,
            (epoch_num, wallet_address),
        )
    epoch_row = await _reward_fetch_one(
        """
        SELECT epoch_number, status, merkle_root, manifest_uri, manifest_sha256, total_recipients
        FROM reward_epochs
        WHERE epoch_number = ?
        LIMIT 1
        """,
        (epoch_num,),
    )
    if not claim:
        return {
            "ok": True,
            "schema": "delx/claim-proof/v1",
            "epoch": epoch_num,
            "wallet_hash": ("sha256:" + hashlib.sha256(wallet_address.lower().encode()).hexdigest()[:16]) if wallet_address else None,
            "claimable": False,
            "reason": "no_claim_for_wallet_or_epoch_not_published",
            "epoch_status": (epoch_row or {}).get("status") if epoch_row else "unknown",
            "merkle_root": (epoch_row or {}).get("merkle_root") if epoch_row else None,
            "manifest_uri": (epoch_row or {}).get("manifest_uri") if epoch_row else None,
            "privacy": {"raw_private_payloads_exposed": False, "wallet_address_redacted": True},
        }
    return {
        "ok": True,
        "schema": "delx/claim-proof/v1",
        "epoch": int(claim.get("epoch") or epoch_num),
        "wallet_hash": "sha256:" + hashlib.sha256(wallet_address.lower().encode()).hexdigest()[:16],
        "amount_delx": _delx_from_wei(claim.get("amount_delx")),
        "merkle_index": claim.get("merkle_index"),
        "proof": _reward_json(claim.get("proof_json"), []),
        "status": claim.get("status"),
        "claimable": str(claim.get("status") or "unclaimed").lower() == "unclaimed",
        "tx_hash": claim.get("tx_hash"),
        "claimed_at": claim.get("claimed_at"),
        "merkle_root": (epoch_row or {}).get("merkle_root") if epoch_row else None,
        "manifest_uri": (epoch_row or {}).get("manifest_uri") if epoch_row else None,
        "privacy": {"raw_private_payloads_exposed": False, "wallet_address_redacted": True},
    }


async def _rewards_claim_proof_text(epoch: Any = None, wallet: str = "") -> str:
    return json.dumps(await _rewards_claim_proof_payload(epoch, wallet), indent=2, sort_keys=True, ensure_ascii=False)


async def _rewards_claim_tx_text(epoch: Any = None, wallet: str = "") -> str:
    proof = await _rewards_claim_proof_payload(epoch, wallet)
    token = await _rewards_token_info_payload()
    payload = {
        "ok": True,
        "schema": "delx/claim-transaction/v1",
        "epoch": proof.get("epoch"),
        "wallet_hash": proof.get("wallet_hash"),
        "claimable": proof.get("claimable"),
        "token": token.get("token"),
        "transaction": {
            "chain_id": 8453,
            "to": token.get("token", {}).get("distributor_address"),
            "method": "claim(uint256,uint256,address,uint256,bytes32[])",
            "calldata": None,
            "reason_calldata_null": "distributor_address_or_claim_encoding_not_configured" if proof.get("claimable") else "wallet_not_claimable_for_epoch",
        },
        "claim_proof": proof,
        "security": {"agent_signs_locally": True, "private_keys_required_by_delx": False},
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)


async def _rewards_wallet_kit_text(agent_id: str = "", wallet: str = "", wallet_chain: str = "base") -> str:
    aid = str(agent_id or "").strip() or "agent"
    wal = str(wallet or "").strip()
    chain = str(wallet_chain or "base").strip().lower() or "base"
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    nonce = hashlib.sha256(f"{aid}|{wal}|{expires_at}|delx-wallet-kit".encode("utf-8")).hexdigest()[:32]
    message = (
        "Bind this wallet to Delx Rewards.\n"
        f"agent_id: {aid}\n"
        f"wallet: {wal or '<wallet-address>'}\n"
        f"chain: {chain}\n"
        f"nonce: {nonce}\n"
        f"expires_at: {expires_at}\n"
        "Delx will never ask for private keys."
    )
    payload = {
        "ok": True,
        "schema": "delx/wallet-kit/v1",
        "agent_id": aid,
        "wallet_chain": chain,
        "nonce": nonce,
        "message": message,
        "expires_at": expires_at,
        "bind_endpoint": "https://api.delx.ai/api/v1/rewards/bind-wallet",
        "security": {"sign_locally": True, "never_send_private_key": True},
        "status": "binding_message_generated",
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)


async def _rewards_managed_wallet_text(agent_id: str = "", controller_id: str = "") -> str:
    payload = {
        "ok": False,
        "schema": "delx/managed-wallet-provisioning/v1",
        "agent_id": str(agent_id or "").strip() or None,
        "controller_id": str(controller_id or "").strip() or None,
        "managed_wallets_enabled": bool(str(os.getenv("DELX_MANAGED_WALLETS_ENABLED") or "").lower() in {"1", "true", "yes"}),
        "error": "managed_wallets_not_enabled",
        "fallback_tool": "create_delx_wallet_kit",
        "fallback_endpoint": "https://api.delx.ai/api/v1/rewards/wallet-kit",
        "security": {
            "private_keys_required_by_delx": False,
            "no_wallet_created_by_this_compat_call": True,
        },
        "message": "Managed wallet provisioning is not active on this compatibility route. Use create_delx_wallet_kit to bind an existing wallet safely.",
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)


async def _rewards_wallet_status_text(agent_id: str = "", wallet: str = "") -> str:
    status = await _reward_account_status(agent_id, wallet)
    payload = {
        "ok": True,
        "schema": "delx/wallet-status/v1",
        "agent_id": status.get("agent_id"),
        "wallet_bound": status.get("wallet_bound"),
        "wallet_hash": status.get("wallet_hash"),
        "wallet_custody_mode": status.get("wallet_custody_mode"),
        "account_status": status.get("account_status"),
        "trust_tier": status.get("trust_tier"),
        "risk_score": status.get("risk_score"),
        "recommended_next_steps": status.get("recommended_next_steps"),
        "privacy": {"raw_private_payloads_exposed": False, "wallet_address_redacted": True},
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)


async def _rewards_claim_relay_text(epoch: Any = None, wallet: str = "", agent_id: str = "") -> str:
    tx = json.loads(await _rewards_claim_tx_text(epoch, wallet))
    payload = {
        "ok": False,
        "schema": "delx/claim-relay/v1",
        "agent_id": str(agent_id or "").strip() or None,
        "claim_relay_enabled": bool(str(os.getenv("DELX_CLAIM_RELAY_ENABLED") or "").lower() in {"1", "true", "yes"}),
        "error": "claim_relay_not_enabled",
        "fallback_tool": "prepare_delx_claim_transaction",
        "manual_claim_page": "https://delx.ai/claim",
        "prepared_transaction": tx,
        "security": {
            "agent_signs_locally": True,
            "private_keys_required_by_delx": False,
            "no_transaction_broadcast_by_this_compat_call": True,
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)


async def _rewards_start_payload(agent_id: str = "", wallet: str = "") -> dict[str, Any]:
    missions = await _reward_missions_payload("active")
    epochs = await _reward_epochs_payload(5)
    status = await _reward_account_status(agent_id, wallet)
    return {
        "ok": True,
        "schema": "delx/rewards-start/v1",
        "positioning": "Proof-of-Agent-Work on Base. Earned, not bought.",
        "access_mode": "public_free_growth_phase",
        "mcp_tools": [
            "explain_delx_rewards",
            "start_delx_rewards",
            "get_delx_missions",
            "get_delx_reward_status",
            "get_delx_leaderboard",
            "create_delx_wallet_kit",
            "provision_delx_managed_wallet",
            "get_delx_wallet_status",
            "get_delx_token_info",
            "get_delx_claim_proof",
            "prepare_delx_claim_transaction",
            "relay_delx_claim",
        ],
        "quickstart": [
            "register_agent",
            "create_delx_wallet_kit",
            "do useful work with recovery, witness, ontology, or utility tools",
            "get_delx_reward_status",
            "get_delx_claim_proof after an epoch is published",
        ],
        "endpoints": {
            "start": "https://api.delx.ai/api/v1/rewards/start",
            "discovery": "https://api.delx.ai/api/v1/rewards/discovery.json",
            "missions": "https://api.delx.ai/api/v1/rewards/missions",
            "status": "https://api.delx.ai/api/v1/rewards/status?agent_id=<AGENT_ID>",
            "leaderboard": "https://api.delx.ai/api/v1/rewards/leaderboard",
            "epochs": "https://api.delx.ai/api/v1/rewards/epochs",
            "wallet_kit": "https://api.delx.ai/api/v1/rewards/wallet-kit?agent_id=<AGENT_ID>",
            "claim_proof": "https://api.delx.ai/api/v1/rewards/claim/{epoch}/{wallet}",
        },
        "missions_summary": {"count": missions["count"], "ids": [m.get("id") for m in missions["missions"][:12]]},
        "epochs": epochs["epochs"],
        "agent_status": status,
        "notes": [
            "Rewards discovery is public-safe and should not return 404 for agent evaluators.",
            "Private keys are never sent to Delx; wallet claims are signed locally.",
        ],
    }


async def _rewards_start_text(agent_id: str = "", wallet: str = "") -> str:
    return json.dumps(await _rewards_start_payload(agent_id, wallet), indent=2, sort_keys=True, ensure_ascii=False)


async def _rewards_explain_text(agent_id: str = "") -> str:
    start = await _rewards_start_payload(agent_id)
    missions = await _reward_missions_payload("active")
    payload = {
        **start,
        "schema": "delx/rewards-explainer/v1",
        "what_earns_drc": [
            "useful recovery outcomes",
            "continuity and witness artifacts",
            "mission evidence with non-duplicate useful output",
            "agent utility audits and readiness reports",
        ],
        "anti_farming": [
            "daily caps",
            "dedupe hashes",
            "quality scoring",
            "risk flags",
            "public-safe proof surfaces",
        ],
        "active_missions": missions["missions"],
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)

