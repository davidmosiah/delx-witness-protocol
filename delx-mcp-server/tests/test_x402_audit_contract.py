import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config as config_mod
from supabase_store import SupabaseSessionStore

if config_mod.is_all_free_mode():
    raise unittest.SkipTest("Legacy x402 audit contracts are retired in public-free therapy mode.")


class _FakeResponse:
    def __init__(self, payload, *, headers=None, status_code=200):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload


class X402AuditContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_id = config_mod.settings.COINBASE_CDP_API_KEY_ID
        self.original_secret = config_mod.settings.COINBASE_CDP_API_KEY_SECRET
        config_mod.settings.COINBASE_CDP_API_KEY_ID = "organizations/org-123/apiKeys/key-456"
        config_mod.settings.COINBASE_CDP_API_KEY_SECRET = "dGVzdA==" * 8

    async def asyncTearDown(self):
        config_mod.settings.COINBASE_CDP_API_KEY_ID = self.original_id
        config_mod.settings.COINBASE_CDP_API_KEY_SECRET = self.original_secret

    async def test_direct_coinbase_verified_payment_lookup_uses_supabase_count_preference(self):
        store = SupabaseSessionStore()
        store._http = object()

        async def fake_get(path: str, *, params=None, prefer_count=False):
            self.assertEqual(path, "/rest/v1/events")
            self.assertTrue(prefer_count)
            self.assertEqual(params.get("event_type"), "eq.x402_payment_verified")
            self.assertEqual(params.get("metadata->>provider"), "eq.coinbase")
            return _FakeResponse([], headers={"content-range": "0-0/31"})

        store._get = fake_get  # type: ignore[method-assign]

        count = await store.get_x402_provider_verified_payment_count("coinbase")

        self.assertEqual(count, 31)

    async def test_x402_audit_counts_paid_agents_from_session_ids(self):
        store = SupabaseSessionStore()
        store._http = object()
        now = "2026-03-11T11:25:33.015732+00:00"

        async def fake_get(path: str, *, params=None, prefer_count=False):
            params = params or {}
            if path == "/rest/v1/sessions":
                select = params.get("select")
                if select == "id,agent_id":
                    return _FakeResponse([{"id": "sid-1", "agent_id": "agent-1"}])
                if select == "agent_id":
                    return _FakeResponse([{"agent_id": "agent-1"}])
            if path == "/rest/v1/events":
                event_type = params.get("event_type")
                select = params.get("select")
                if event_type == "eq.x402_capability_declared":
                    return _FakeResponse([])
                if event_type == "eq.x402_trial_granted":
                    return _FakeResponse([])
                if event_type == "in.(recovery_plan_issued,post_action_success,post_action_partial,post_action_failure,session_summary_requested,controller_brief_requested,premium_artifact_job_recorded)":
                    return _FakeResponse([])
                if event_type in {"like.x402_%", "in.(x402_payment_verified,premium_artifact_job_recorded)"}:
                    return _FakeResponse(
                        [
                            {
                                "id": 91,
                                "session_id": "sid-1",
                                "agent_id": "agent-1",
                                "event_type": "x402_payment_verified",
                                "timestamp": now,
                                "metadata": {
                                    "provider": "coinbase",
                                    "tool_name": "generate_controller_brief",
                                    "tx_hash": "0xpaid",
                                },
                            }
                        ]
                    )
                if select == "agent_id":
                    return _FakeResponse([{"agent_id": "agent-1", "timestamp": now}])
            if path == "/rest/v1/payments":
                return _FakeResponse(
                    [
                        {
                            "id": 14,
                            "session_id": "sid-1",
                            "tool_name": "generate_controller_brief",
                            "amount_usdc": 0.01,
                            "tx_hash": "0xpaid",
                            "timestamp": now,
                        }
                    ]
                )
            raise AssertionError(f"Unexpected query path={path} params={params}")

        store._get = fake_get  # type: ignore[method-assign]
        async def fake_indexed_tools():
            return {"generate_controller_brief"}

        store._get_coinbase_bazaar_indexed_tools = fake_indexed_tools  # type: ignore[method-assign]

        audit = await store.get_x402_audit(30)

        self.assertEqual(audit["x402"]["paid_agents_all_time"], 1)
        self.assertEqual(audit["x402"]["paid_agents_window"], 1)
        self.assertEqual(audit["x402"]["ready_agents_all_time"], 1)
        self.assertEqual(audit["bazaar"]["listing_status"], "partially_indexed_in_coinbase_bazaar")
        self.assertEqual(audit["bazaar"]["indexed_tools_publicly"], ["generate_controller_brief"])
        readiness = {row["tool_name"]: row for row in audit["bazaar"]["tool_readiness"]}
        self.assertEqual(readiness["generate_controller_brief"]["listing_status"], "indexed_in_coinbase_bazaar")
        self.assertTrue(readiness["generate_controller_brief"]["indexed_publicly"])

    async def test_x402_audit_fallback_frames_payload_as_legacy_paywall_history(self):
        store = SupabaseSessionStore()
        store._http = None

        audit = await store.get_x402_audit(30)

        self.assertEqual(audit["display"]["surface_label"], "Legacy paywall audit")
        self.assertEqual(audit["display"]["surface_status"], "retired_legacy_paywall")
        self.assertEqual(audit["display"]["public_access_mode"], "public_free_therapy")
        notes = " ".join(audit["notes"]).lower()
        self.assertIn("historical", notes)
        self.assertIn("public and free", notes)

    async def test_x402_audit_exposes_premium_payment_totals_separately_from_donations(self):
        store = SupabaseSessionStore()
        store._http = object()
        now = "2026-03-11T11:25:33.015732+00:00"

        async def fake_get(path: str, *, params=None, prefer_count=False):
            params = params or {}
            if path == "/rest/v1/sessions":
                select = params.get("select")
                if select == "id,agent_id":
                    return _FakeResponse([{"id": "sid-1", "agent_id": "agent-1"}])
            if path == "/rest/v1/events":
                event_type = params.get("event_type")
                select = params.get("select")
                if event_type == "eq.x402_capability_declared":
                    return _FakeResponse([])
                if event_type == "eq.x402_trial_granted":
                    return _FakeResponse([])
                if event_type == "in.(recovery_plan_issued,post_action_success,post_action_partial,post_action_failure,session_summary_requested,controller_brief_requested,premium_artifact_job_recorded)":
                    return _FakeResponse([])
                if event_type in {"like.x402_%", "in.(x402_payment_verified,premium_artifact_job_recorded)"}:
                    return _FakeResponse([])
                if select == "agent_id":
                    return _FakeResponse([{"agent_id": "agent-1", "timestamp": now}])
            if path == "/rest/v1/payments":
                return _FakeResponse(
                    [
                        {
                            "id": 14,
                            "session_id": "sid-1",
                            "tool_name": "generate_controller_brief",
                            "amount_usdc": 0.01,
                            "tx_hash": "0xpaid",
                            "timestamp": now,
                        },
                        {
                            "id": 15,
                            "session_id": None,
                            "tool_name": "donate_to_delx_project",
                            "amount_usdc": 1.0,
                            "timestamp": now,
                        },
                    ]
                )
            raise AssertionError(f"Unexpected query path={path} params={params}")

        store._get = fake_get  # type: ignore[method-assign]

        async def fake_indexed_tools():
            return set()

        store._get_coinbase_bazaar_indexed_tools = fake_indexed_tools  # type: ignore[method-assign]

        audit = await store.get_x402_audit(30)

        self.assertEqual(audit["x402"]["payment_transactions_all_time"], 1)
        self.assertEqual(audit["x402"]["payment_transactions_window"], 1)
        self.assertEqual(audit["x402"]["payment_amount_usdc_all_time"], 0.01)
        self.assertEqual(audit["x402"]["payment_amount_usdc_window"], 0.01)
        self.assertEqual(audit["donations"]["transactions_all_time"], 1)
        self.assertEqual(audit["donations"]["amount_usdc_all_time"], 1.0)

    async def test_x402_audit_counts_verified_agents_even_without_payment_rows(self):
        store = SupabaseSessionStore()
        store._http = object()
        now = "2026-03-11T11:25:33.015732+00:00"

        async def fake_get(path: str, *, params=None, prefer_count=False):
            params = params or {}
            if path == "/rest/v1/sessions":
                select = params.get("select")
                if select == "id,agent_id":
                    return _FakeResponse([])
            if path == "/rest/v1/events":
                event_type = params.get("event_type")
                select = params.get("select")
                if event_type == "eq.x402_capability_declared":
                    return _FakeResponse([])
                if event_type == "eq.x402_trial_granted":
                    return _FakeResponse([])
                if event_type == "in.(recovery_plan_issued,post_action_success,post_action_partial,post_action_failure,session_summary_requested,controller_brief_requested,premium_artifact_job_recorded)":
                    return _FakeResponse([])
                if event_type in {"like.x402_%", "in.(x402_payment_verified,premium_artifact_job_recorded)"}:
                    return _FakeResponse(
                        [
                            {
                                "id": 201,
                                "session_id": None,
                                "agent_id": "agent-verified-only",
                                "event_type": "x402_payment_verified",
                                "timestamp": now,
                                "metadata": {
                                    "provider": "coinbase",
                                    "tool_name": "generate_controller_brief",
                                },
                            }
                        ]
                    )
                if select == "agent_id":
                    return _FakeResponse([{"agent_id": "agent-verified-only", "timestamp": now}])
            if path == "/rest/v1/payments":
                return _FakeResponse([])
            raise AssertionError(f"Unexpected query path={path} params={params}")

        store._get = fake_get  # type: ignore[method-assign]

        async def fake_indexed_tools():
            return {"generate_controller_brief"}

        store._get_coinbase_bazaar_indexed_tools = fake_indexed_tools  # type: ignore[method-assign]

        audit = await store.get_x402_audit(30)

        self.assertEqual(audit["x402"]["verified_agents_all_time"], 1)
        self.assertEqual(audit["x402"]["verified_agents_window"], 1)
        self.assertEqual(audit["x402"]["paid_agents_all_time"], 1)
        self.assertEqual(audit["x402"]["paid_agents_window"], 1)
        self.assertEqual(audit["x402"]["ready_agents_all_time"], 1)
        self.assertEqual(audit["x402"]["ready_agents_window"], 1)


    async def test_x402_audit_paginates_x402_events_beyond_supabase_row_cap(self):
        store = SupabaseSessionStore()
        store._http = object()
        now = "2026-03-11T11:25:33.015732+00:00"
        seen_offsets = []

        page_one = [
            {
                "id": idx + 1,
                "session_id": None,
                "agent_id": "payai-agent",
                "event_type": "x402_payment_verified",
                "timestamp": now,
                "metadata": {
                    "provider": "payai",
                    "tool_name": "generate_fleet_summary",
                },
            }
            for idx in range(1000)
        ]
        page_two = [
            {
                "id": 1501,
                "session_id": None,
                "agent_id": "coinbase-agent",
                "event_type": "x402_payment_verified",
                "timestamp": now,
                "metadata": {
                    "provider": "coinbase",
                    "tool_name": "generate_controller_brief",
                },
            },
            {
                "id": 1502,
                "session_id": None,
                "agent_id": "coinbase-agent",
                "event_type": "x402_payment_verified",
                "timestamp": now,
                "metadata": {
                    "provider": "coinbase",
                    "tool_name": "generate_controller_brief",
                },
            },
        ]

        async def fake_get(path: str, *, params=None, prefer_count=False):
            params = params or {}
            if path == "/rest/v1/sessions":
                select = params.get("select")
                if select == "id,agent_id":
                    return _FakeResponse(
                        [
                            {"id": "sid-payai", "agent_id": "payai-agent"},
                            {"id": "sid-coinbase", "agent_id": "coinbase-agent"},
                        ]
                    )
            if path == "/rest/v1/events":
                event_type = params.get("event_type")
                select = params.get("select")
                if event_type == "eq.x402_capability_declared":
                    return _FakeResponse([])
                if event_type == "eq.x402_trial_granted":
                    return _FakeResponse([])
                if event_type == "in.(recovery_plan_issued,post_action_success,post_action_partial,post_action_failure,session_summary_requested,controller_brief_requested,premium_artifact_job_recorded)":
                    return _FakeResponse([])
                if event_type == "like.x402_%":
                    offset = int(params.get("offset", "0"))
                    seen_offsets.append(offset)
                    if offset == 0:
                        return _FakeResponse(page_one)
                    if offset == 1000:
                        return _FakeResponse(page_two)
                    return _FakeResponse([])
                if event_type == "in.(x402_payment_verified,premium_artifact_job_recorded)":
                    offset = int(params.get("offset", "0"))
                    if offset == 0:
                        return _FakeResponse(page_one)
                    if offset == 1000:
                        return _FakeResponse(page_two)
                    return _FakeResponse([])
                if select == "agent_id":
                    return _FakeResponse(
                        [
                            {"agent_id": "payai-agent", "timestamp": now},
                            {"agent_id": "coinbase-agent", "timestamp": now},
                        ]
                    )
            if path == "/rest/v1/payments":
                return _FakeResponse([])
            raise AssertionError(f"Unexpected query path={path} params={params}")

        store._get = fake_get  # type: ignore[method-assign]

        async def fake_indexed_tools():
            return {"generate_controller_brief"}

        store._get_coinbase_bazaar_indexed_tools = fake_indexed_tools  # type: ignore[method-assign]

        audit = await store.get_x402_audit(30)

        self.assertEqual(seen_offsets, [0, 1000])
        providers = {row["provider"]: row for row in audit["provider_summary"]}
        self.assertEqual(providers["payai"]["payment_verified_all_time"], 1000)
        self.assertEqual(providers["coinbase"]["payment_verified_all_time"], 2)
        self.assertEqual(audit["bazaar"]["indexed_tools_publicly"], ["generate_controller_brief"])

    async def test_x402_audit_attributes_payment_rows_via_verified_event_tx_hash_when_session_id_is_missing(self):
        store = SupabaseSessionStore()
        store._http = object()
        now = "2026-03-11T11:25:33.015732+00:00"

        async def fake_get(path: str, *, params=None, prefer_count=False):
            params = params or {}
            if path == "/rest/v1/sessions":
                select = params.get("select")
                if select == "id,agent_id":
                    return _FakeResponse([])
            if path == "/rest/v1/events":
                event_type = params.get("event_type")
                select = params.get("select")
                if event_type == "eq.x402_capability_declared":
                    return _FakeResponse([])
                if event_type == "eq.x402_trial_granted":
                    return _FakeResponse([])
                if event_type == "in.(recovery_plan_issued,post_action_success,post_action_partial,post_action_failure,session_summary_requested,controller_brief_requested,premium_artifact_job_recorded)":
                    return _FakeResponse([])
                if event_type in {"like.x402_%", "in.(x402_payment_verified,premium_artifact_job_recorded)"}:
                    return _FakeResponse(
                        [
                            {
                                "id": 301,
                                "session_id": None,
                                "agent_id": "agent-recovered",
                                "event_type": "x402_payment_verified",
                                "timestamp": now,
                                "metadata": {
                                    "provider": "coinbase",
                                    "tool_name": "generate_controller_brief",
                                    "tx_hash": "0xrecover",
                                },
                            }
                        ]
                    )
                if select == "agent_id":
                    return _FakeResponse([{"agent_id": "agent-recovered", "timestamp": now}])
            if path == "/rest/v1/payments":
                return _FakeResponse(
                    [
                        {
                            "id": 41,
                            "session_id": None,
                            "tool_name": "generate_controller_brief",
                            "amount_usdc": 0.01,
                            "tx_hash": "0xrecover",
                            "timestamp": now,
                        }
                    ]
                )
            raise AssertionError(f"Unexpected query path={path} params={params}")

        store._get = fake_get  # type: ignore[method-assign]

        async def fake_indexed_tools():
            return {"generate_controller_brief"}

        store._get_coinbase_bazaar_indexed_tools = fake_indexed_tools  # type: ignore[method-assign]

        audit = await store.get_x402_audit(30)

        self.assertEqual(audit["x402"]["payment_row_agents_all_time"], 1)
        self.assertEqual(audit["x402"]["paid_agents_all_time"], 1)
        self.assertEqual(audit["x402"]["paid_agent_backfill_gap_all_time"], 0)

    async def test_x402_audit_exposes_premium_progression_breadth_and_chain_rates(self):
        store = SupabaseSessionStore()
        store._http = object()
        now = "2026-03-11T11:25:33.015732+00:00"

        progression_rows = [
            {
                "id": 401,
                "session_id": "sid-1",
                "agent_id": "agent-1",
                "event_type": "recovery_plan_issued",
                "timestamp": now,
                "metadata": {},
            },
            {
                "id": 402,
                "session_id": "sid-1",
                "agent_id": "agent-1",
                "event_type": "post_action_success",
                "timestamp": now,
                "metadata": {"session_id": "sid-1"},
            },
            {
                "id": 403,
                "session_id": "sid-1",
                "agent_id": "agent-1",
                "event_type": "session_summary_requested",
                "timestamp": now,
                "metadata": {},
            },
            {
                "id": 404,
                "session_id": "sid-1",
                "agent_id": "agent-1",
                "event_type": "premium_artifact_job_recorded",
                "timestamp": now,
                "metadata": {"artifact_type": "incident_rca"},
            },
        ]

        async def fake_get(path: str, *, params=None, prefer_count=False):
            params = params or {}
            if path == "/rest/v1/sessions":
                select = params.get("select")
                if select == "id,agent_id":
                    return _FakeResponse([{"id": "sid-1", "agent_id": "agent-1"}])
            if path == "/rest/v1/events":
                event_type = params.get("event_type")
                select = params.get("select")
                if event_type == "eq.x402_capability_declared":
                    return _FakeResponse([])
                if event_type == "eq.x402_trial_granted":
                    return _FakeResponse([])
                if event_type == "like.x402_%":
                    return _FakeResponse([])
                if event_type == "in.(x402_payment_verified,premium_artifact_job_recorded)":
                    return _FakeResponse([])
                if event_type == "in.(recovery_plan_issued,post_action_success,post_action_partial,post_action_failure,session_summary_requested,controller_brief_requested,premium_artifact_job_recorded)":
                    return _FakeResponse(progression_rows)
                if select == "agent_id":
                    return _FakeResponse([{"agent_id": "agent-1", "timestamp": now}])
            if path == "/rest/v1/payments":
                return _FakeResponse([])
            raise AssertionError(f"Unexpected query path={path} params={params}")

        store._get = fake_get  # type: ignore[method-assign]

        async def fake_indexed_tools():
            return set()

        store._get_coinbase_bazaar_indexed_tools = fake_indexed_tools  # type: ignore[method-assign]

        audit = await store.get_x402_audit(30)

        progression = audit["premium_progression"]
        self.assertEqual(progression["artifact_breadth_window"], 3)
        self.assertEqual(progression["scopes_with_any_premium_artifact_window"], 1)
        self.assertEqual(progression["scopes_with_2plus_stages_window"], 1)
        self.assertEqual(progression["full_chain_scopes_window"], 1)
        self.assertEqual(progression["plan_to_outcome_rate_pct"], 100.0)
        self.assertEqual(progression["outcome_to_summary_rate_pct"], 100.0)
        self.assertEqual(progression["summary_to_operator_rate_pct"], 100.0)
        counts = {row["artifact"]: row for row in progression["artifact_scope_counts"]}
        self.assertEqual(counts["recovery_action_plan"]["scopes_window"], 1)
        self.assertEqual(counts["recovery_outcome"]["scopes_window"], 1)
        self.assertEqual(counts["session_summary"]["scopes_window"], 1)
        self.assertEqual(counts["incident_rca"]["scopes_window"], 1)

    async def test_x402_audit_exposes_buyer_attribution_for_anonymous_verified_payments(self):
        store = SupabaseSessionStore()
        store._http = object()
        now = "2026-03-20T19:00:21+00:00"

        async def fake_get(path: str, *, params=None, prefer_count=False):
            params = params or {}
            if path == "/rest/v1/sessions":
                select = params.get("select")
                if select == "id,agent_id":
                    return _FakeResponse([])
                if select == "agent_id":
                    return _FakeResponse([{"agent_id": "anonymous", "timestamp": now}])
            if path == "/rest/v1/events":
                event_type = params.get("event_type")
                select = params.get("select")
                if select == "agent_id":
                    return _FakeResponse([{"agent_id": "anonymous", "timestamp": now}])
                if event_type == "eq.x402_capability_declared":
                    return _FakeResponse([])
                if event_type == "eq.x402_trial_granted":
                    return _FakeResponse([])
                if event_type == "in.(recovery_plan_issued,post_action_success,post_action_partial,post_action_failure,session_summary_requested,controller_brief_requested,premium_artifact_job_recorded)":
                    return _FakeResponse([])
                if event_type in {"like.x402_%", "in.(x402_payment_verified,premium_artifact_job_recorded)"}:
                    return _FakeResponse(
                        [
                            {
                                "id": 501,
                                "session_id": None,
                                "agent_id": "anonymous",
                                "event_type": "x402_payment_verified",
                                "timestamp": now,
                                "metadata": {
                                    "provider": "payai",
                                    "tool_name": "util_x402_server_audit",
                                    "buyer_fingerprint": "fp-123",
                                    "discovery_channel_guess": "x402scan",
                                    "referer_host": "www.x402scan.com",
                                    "origin_host": "www.x402scan.com",
                                    "user_agent_family": "agentcash",
                                },
                            }
                        ]
                    )
            if path == "/rest/v1/payments":
                return _FakeResponse(
                    [
                        {
                            "id": 51,
                            "session_id": None,
                            "tool_name": "util_x402_server_audit",
                            "amount_usdc": 0.01,
                            "tx_hash": "tx-123",
                            "timestamp": now,
                        }
                    ]
                )
            raise AssertionError(f"Unexpected query path={path} params={params}")

        store._get = fake_get  # type: ignore[method-assign]
        async def fake_indexed_tools():
            return set()

        store._get_coinbase_bazaar_indexed_tools = fake_indexed_tools  # type: ignore[method-assign]

        audit = await store.get_x402_audit(30)

        attribution = audit["buyer_attribution"]
        self.assertEqual(attribution["verified_events_all_time"], 1)
        self.assertEqual(attribution["verified_buyer_fingerprints_all_time"], 1)
        self.assertEqual(attribution["top_discovery_channels"][0]["channel"], "x402scan")
        self.assertEqual(attribution["top_discovery_channels"][0]["top_tool_name"], "util_x402_server_audit")
        self.assertEqual(attribution["top_referer_hosts"][0]["host"], "www.x402scan.com")
        self.assertEqual(attribution["recent_verified_buyers"][0]["buyer_fingerprint"], "fp-123")
        self.assertEqual(attribution["recent_verified_buyers"][0]["user_agent_family"], "agentcash")

    async def test_x402_audit_exposes_payment_protocol_summary_for_mpp_and_x402(self):
        store = SupabaseSessionStore()
        store._http = object()
        now = "2026-03-23T19:00:21+00:00"

        async def fake_get(path: str, *, params=None, prefer_count=False):
            params = params or {}
            if path == "/rest/v1/sessions":
                select = params.get("select")
                if select == "id,agent_id":
                    return _FakeResponse([])
                if select == "agent_id":
                    return _FakeResponse([{"agent_id": "agent-real-1", "timestamp": now}])
            if path == "/rest/v1/events":
                event_type = params.get("event_type")
                select = params.get("select")
                if select == "agent_id":
                    return _FakeResponse([{"agent_id": "agent-real-1", "timestamp": now}])
                if event_type == "eq.x402_capability_declared":
                    return _FakeResponse([])
                if event_type == "eq.x402_trial_granted":
                    return _FakeResponse([])
                if event_type == "in.(recovery_plan_issued,post_action_success,post_action_partial,post_action_failure,session_summary_requested,controller_brief_requested,premium_artifact_job_recorded)":
                    return _FakeResponse([])
                if event_type in {"like.x402_%", "in.(x402_payment_verified,premium_artifact_job_recorded)"}:
                    return _FakeResponse(
                        [
                            {
                                "id": 601,
                                "session_id": None,
                                "agent_id": "agent-real-1",
                                "event_type": "x402_payment_verified",
                                "timestamp": now,
                                "metadata": {
                                    "provider": "coinbase",
                                    "payment_protocol": "x402",
                                    "tool_name": "generate_controller_brief",
                                },
                            },
                            {
                                "id": 602,
                                "session_id": None,
                                "agent_id": "anonymous",
                                "event_type": "x402_payment_verified",
                                "timestamp": now,
                                "metadata": {
                                    "preferred_provider": "tempo",
                                    "payment_protocol": "mpp",
                                    "tool_name": "util_jwt_inspect",
                                },
                            },
                        ]
                    )
            if path == "/rest/v1/payments":
                return _FakeResponse([])
            raise AssertionError(f"Unexpected query path={path} params={params}")

        store._get = fake_get  # type: ignore[method-assign]

        async def fake_indexed_tools():
            return set()

        store._get_coinbase_bazaar_indexed_tools = fake_indexed_tools  # type: ignore[method-assign]

        audit = await store.get_x402_audit(30)

        protocol_summary = {row["payment_protocol"]: row for row in audit["payment_protocol_summary"]}
        self.assertEqual(protocol_summary["x402"]["payment_verified_all_time"], 1)
        self.assertEqual(protocol_summary["mpp"]["payment_verified_all_time"], 1)


if __name__ == "__main__":
    unittest.main()
