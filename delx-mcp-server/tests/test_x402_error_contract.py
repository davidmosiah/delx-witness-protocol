import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config as config_mod
from config import get_tool_pricing_payload
from supabase_store import SupabaseSessionStore
from x402_guard import _build_verify_failed_response

if config_mod.is_all_free_mode():
    raise unittest.SkipTest("Legacy x402 error contracts are retired in public-free therapy mode.")


class _FakeResponse:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload


class X402ErrorContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_x402_error_metrics_uses_attempted_denominator_for_verify_failure_rate(self):
        store = SupabaseSessionStore()
        store._http = object()
        now = "2026-03-11T11:25:33.015732+00:00"

        rows = [
            {
                "agent_id": "agent-real-1",
                "event_type": "x402_payment_required",
                "timestamp": now,
                "metadata": {"protocol": "rest", "tool_name": "generate_controller_brief", "provider": "coinbase", "source": "prod-sdk"},
            },
            {
                "agent_id": "agent-real-1",
                "event_type": "x402_payment_attempted",
                "timestamp": now,
                "metadata": {"protocol": "rest", "tool_name": "generate_controller_brief", "provider": "coinbase", "source": "prod-sdk"},
            },
            {
                "agent_id": "agent-real-2",
                "event_type": "x402_payment_attempted",
                "timestamp": now,
                "metadata": {"protocol": "rest", "tool_name": "generate_controller_brief", "provider": "coinbase", "source": "prod-sdk"},
            },
            {
                "agent_id": "agent-real-2",
                "event_type": "x402_verify_failed",
                "timestamp": now,
                "metadata": {
                    "protocol": "rest",
                    "tool_name": "generate_controller_brief",
                    "provider": "coinbase",
                    "source": "prod-sdk",
                    "failure_code": "verification_failed",
                },
            },
        ]

        async def fake_get(path: str, *, params=None, prefer_count=False):
            if path == "/rest/v1/events":
                return _FakeResponse(rows)
            raise AssertionError(f"Unexpected query path={path} params={params}")

        store._get = fake_get  # type: ignore[method-assign]

        metrics = await store.get_x402_error_metrics(24)

        self.assertEqual(metrics["totals"]["verify_failure_rate_pct"], 50.0)
        self.assertEqual(metrics["clean_totals"]["verify_failure_rate_pct"], 50.0)
        protocols = {row["payment_protocol"]: row for row in metrics["by_payment_protocol"]}
        self.assertEqual(protocols["x402"]["payment_attempted"], 2)
        self.assertEqual(protocols["x402"]["verify_failed"], 1)

    async def test_x402_error_metrics_exposes_clean_totals_and_sources(self):
        store = SupabaseSessionStore()
        store._http = object()
        now = "2026-03-11T11:25:33.015732+00:00"

        rows = [
            {
                "agent_id": "agent-real-1",
                "event_type": "x402_payment_required",
                "timestamp": now,
                "metadata": {"protocol": "rest", "tool_name": "generate_controller_brief", "provider": "coinbase", "source": "prod-sdk"},
            },
            {
                "agent_id": "agent-real-1",
                "event_type": "x402_payment_attempted",
                "timestamp": now,
                "metadata": {"protocol": "rest", "tool_name": "generate_controller_brief", "provider": "coinbase", "source": "prod-sdk"},
            },
            {
                "agent_id": "agent-real-1",
                "event_type": "x402_payment_verified",
                "timestamp": now,
                "metadata": {"protocol": "rest", "tool_name": "generate_controller_brief", "provider": "coinbase", "source": "prod-sdk"},
            },
            {
                "agent_id": "codex-smoke-buyer",
                "event_type": "x402_payment_required",
                "timestamp": now,
                "metadata": {"protocol": "rest", "tool_name": "generate_controller_brief", "provider": "unknown", "source": "probe"},
            },
            {
                "agent_id": "codex-smoke-buyer",
                "event_type": "x402_verify_failed",
                "timestamp": now,
                "metadata": {"protocol": "rest", "tool_name": "generate_controller_brief", "provider": "unknown", "source": "probe"},
            },
            {
                "agent_id": "",
                "event_type": "x402_payment_required",
                "timestamp": now,
                "metadata": {"protocol": "mcp", "method": "tools/call", "provider": "unknown"},
            },
        ]

        async def fake_get(path: str, *, params=None, prefer_count=False):
            if path == "/rest/v1/events":
                return _FakeResponse(rows)
            raise AssertionError(f"Unexpected query path={path} params={params}")

        store._get = fake_get  # type: ignore[method-assign]

        metrics = await store.get_x402_error_metrics(24)

        self.assertEqual(metrics["totals"]["payment_required"], 3)
        self.assertEqual(metrics["clean_totals"]["payment_required"], 1)
        self.assertEqual(metrics["clean_totals"]["payment_attempted"], 1)
        self.assertEqual(metrics["clean_totals"]["payment_verified"], 1)
        self.assertEqual(metrics["clean_totals"]["verify_failed"], 0)
        sources = {row["source"]: row for row in metrics["by_source"]}
        self.assertEqual(sources["prod-sdk"]["payment_verified"], 1)
        self.assertEqual(sources["probe"]["verify_failed"], 1)
        self.assertEqual(metrics["agent_segments"]["canonical_named_agents"], 1)
        self.assertEqual(metrics["agent_segments"]["synthetic_or_probe_agents"], 1)
        self.assertEqual(metrics["agent_segments"]["anonymous_agents"], 1)

    async def test_x402_error_metrics_exposes_discovery_channels_and_buyer_fingerprints(self):
        store = SupabaseSessionStore()
        store._http = object()
        now = "2026-03-11T11:25:33.015732+00:00"

        rows = [
            {
                "agent_id": "anonymous",
                "event_type": "x402_payment_required",
                "timestamp": now,
                "metadata": {
                    "protocol": "rest",
                    "tool_name": "util_x402_server_audit",
                    "provider": "payai",
                    "source": "rest",
                    "discovery_channel_guess": "x402scan",
                    "buyer_fingerprint": "fp-abc",
                },
            },
            {
                "agent_id": "anonymous",
                "event_type": "x402_payment_verified",
                "timestamp": now,
                "metadata": {
                    "protocol": "rest",
                    "tool_name": "util_x402_server_audit",
                    "provider": "payai",
                    "source": "rest",
                    "discovery_channel_guess": "x402scan",
                    "buyer_fingerprint": "fp-abc",
                },
            },
        ]

        async def fake_get(path: str, *, params=None, prefer_count=False):
            if path == "/rest/v1/events":
                return _FakeResponse(rows)
            raise AssertionError(f"Unexpected query path={path} params={params}")

        store._get = fake_get  # type: ignore[method-assign]

        metrics = await store.get_x402_error_metrics(24)

        channels = {row["channel"]: row for row in metrics["by_discovery_channel"]}
        self.assertEqual(channels["x402scan"]["payment_required"], 1)
        self.assertEqual(channels["x402scan"]["payment_verified"], 1)
        self.assertEqual(metrics["top_buyer_fingerprints"][0]["buyer_fingerprint"], "fp-abc")
        self.assertEqual(metrics["top_buyer_fingerprints"][0]["payment_required"], 1)
        self.assertEqual(metrics["top_buyer_fingerprints"][0]["payment_attempted"], 0)
        self.assertEqual(metrics["top_buyer_fingerprints"][0]["verify_failed"], 0)
        self.assertEqual(metrics["top_buyer_fingerprints"][0]["payment_verified"], 1)
        self.assertEqual(metrics["top_buyer_fingerprints"][0]["channel"], "x402scan")

    async def test_x402_error_metrics_separates_mpp_from_x402_protocol_buckets(self):
        store = SupabaseSessionStore()
        store._http = object()
        now = "2026-03-23T20:25:33.015732+00:00"

        rows = [
            {
                "agent_id": "anonymous",
                "event_type": "x402_payment_required",
                "timestamp": now,
                "metadata": {
                    "protocol": "rest",
                    "tool_name": "util_jwt_inspect",
                    "payment_protocol": "x402_or_mpp",
                    "provider": "unknown",
                    "source": "direct-cli",
                },
            },
            {
                "agent_id": "anonymous",
                "event_type": "x402_payment_attempted",
                "timestamp": now,
                "metadata": {
                    "protocol": "rest",
                    "tool_name": "util_jwt_inspect",
                    "payment_protocol": "mpp",
                    "preferred_provider": "tempo",
                    "source": "direct-cli",
                },
            },
            {
                "agent_id": "anonymous",
                "event_type": "x402_payment_verified",
                "timestamp": now,
                "metadata": {
                    "protocol": "rest",
                    "tool_name": "util_jwt_inspect",
                    "payment_protocol": "mpp",
                    "provider": "tempo",
                    "source": "direct-cli",
                },
            },
            {
                "agent_id": "agent-real-1",
                "event_type": "x402_payment_verified",
                "timestamp": now,
                "metadata": {
                    "protocol": "rest",
                    "tool_name": "generate_controller_brief",
                    "payment_protocol": "x402",
                    "provider": "coinbase",
                    "source": "prod-sdk",
                },
            },
        ]

        async def fake_get(path: str, *, params=None, prefer_count=False):
            if path == "/rest/v1/events":
                return _FakeResponse(rows)
            raise AssertionError(f"Unexpected query path={path} params={params}")

        store._get = fake_get  # type: ignore[method-assign]

        metrics = await store.get_x402_error_metrics(24)

        protocols = {row["payment_protocol"]: row for row in metrics["by_payment_protocol"]}
        self.assertEqual(protocols["x402_or_mpp"]["payment_required"], 1)
        self.assertEqual(protocols["mpp"]["payment_attempted"], 1)
        self.assertEqual(protocols["mpp"]["payment_verified"], 1)
        self.assertEqual(protocols["x402"]["payment_verified"], 1)

    async def test_x402_error_metrics_counts_evaluation_grants_separately(self):
        store = SupabaseSessionStore()
        store._http = object()
        now = "2026-03-11T11:25:33.015732+00:00"

        rows = [
            {
                "agent_id": "agent-real-1",
                "event_type": "x402_eval_granted",
                "timestamp": now,
                "metadata": {
                    "protocol": "mcp",
                    "method": "tools/call",
                    "tool_name": "generate_controller_brief",
                    "source": "x",
                    "cohort": "x_twitter_eval",
                },
            },
            {
                "agent_id": "codex-smoke-buyer",
                "event_type": "x402_eval_granted",
                "timestamp": now,
                "metadata": {
                    "protocol": "rest",
                    "tool_name": "generate_controller_brief",
                    "source": "probe",
                    "cohort": "x_twitter_eval",
                },
            },
        ]

        async def fake_get(path: str, *, params=None, prefer_count=False):
            if path == "/rest/v1/events":
                return _FakeResponse(rows)
            raise AssertionError(f"Unexpected query path={path} params={params}")

        store._get = fake_get  # type: ignore[method-assign]

        metrics = await store.get_x402_error_metrics(24)

        self.assertEqual(metrics["totals"]["eval_granted"], 2)
        self.assertEqual(metrics["clean_totals"]["eval_granted"], 1)
        sources = {row["source"]: row for row in metrics["by_source"]}
        self.assertEqual(sources["x"]["eval_granted"], 1)
        self.assertEqual(sources["probe"]["eval_granted"], 1)
        targets = {(row["protocol"], row["target"]): row for row in metrics["by_target"]}
        self.assertEqual(targets[("mcp", "tools/call")]["eval_granted"], 1)

    async def test_x402_error_metrics_exposes_failure_codes_and_provider_top_reason(self):
        store = SupabaseSessionStore()
        store._http = object()
        now = "2026-03-11T11:25:33.015732+00:00"

        rows = [
            {
                "agent_id": "agent-real-1",
                "event_type": "x402_verify_failed",
                "timestamp": now,
                "metadata": {
                    "protocol": "rest",
                    "tool_name": "generate_controller_brief",
                    "provider": "coinbase",
                    "source": "prod-sdk",
                    "failure_code": "chain_mismatch",
                },
            },
            {
                "agent_id": "agent-real-2",
                "event_type": "x402_verify_failed",
                "timestamp": now,
                "metadata": {
                    "protocol": "mcp",
                    "method": "tools/call",
                    "provider": "coinbase",
                    "source": "prod-sdk",
                    "failure_code": "chain_mismatch",
                },
            },
            {
                "agent_id": "codex-smoke-buyer",
                "event_type": "x402_verify_failed",
                "timestamp": now,
                "metadata": {
                    "protocol": "rest",
                    "tool_name": "generate_controller_brief",
                    "provider": "unknown",
                    "source": "probe",
                    "failure_code": "missing_payment_header",
                },
            },
        ]

        async def fake_get(path: str, *, params=None, prefer_count=False):
            if path == "/rest/v1/events":
                return _FakeResponse(rows)
            raise AssertionError(f"Unexpected query path={path} params={params}")

        store._get = fake_get  # type: ignore[method-assign]

        metrics = await store.get_x402_error_metrics(24)

        failure_codes = {row["failure_code"]: row for row in metrics["by_failure_code"]}
        self.assertEqual(failure_codes["chain_mismatch"]["count"], 2)
        self.assertEqual(failure_codes["chain_mismatch"]["clean_count"], 2)
        self.assertEqual(failure_codes["chain_mismatch"]["provider_count"], 1)
        self.assertEqual(failure_codes["missing_payment_header"]["count"], 1)
        self.assertEqual(failure_codes["missing_payment_header"]["clean_count"], 0)

        providers = {row["provider"]: row for row in metrics["by_provider"]}
        self.assertEqual(providers["coinbase"]["top_failure_code"], "chain_mismatch")
        self.assertEqual(providers["coinbase"]["top_failure_count"], 2)
        self.assertEqual(providers["coinbase"]["failure_codes"][0]["failure_code"], "chain_mismatch")
        self.assertEqual(providers["coinbase"]["failure_codes"][0]["count"], 2)

    async def test_x402_error_metrics_filters_x402_events_in_supabase_query(self):
        store = SupabaseSessionStore()
        store._http = object()

        async def fake_get(path: str, *, params=None, prefer_count=False):
            self.assertEqual(path, "/rest/v1/events")
            self.assertEqual(params.get("event_type"), "like.x402_%")
            return _FakeResponse(
                [
                    {
                        "agent_id": "agent-real-1",
                        "event_type": "x402_payment_required",
                        "timestamp": "2026-03-11T11:25:33.015732+00:00",
                        "metadata": {"protocol": "rest", "tool_name": "generate_controller_brief", "source": "prod-sdk"},
                    }
                ]
            )

        store._get = fake_get  # type: ignore[method-assign]

        metrics = await store.get_x402_error_metrics(24)

        self.assertEqual(metrics["totals"]["payment_required"], 1)


class X402RuntimeErrorPayloadTests(unittest.IsolatedAsyncioTestCase):
    def test_verify_failed_payload_exposes_prescriptive_retry_contract(self):
        pricing_payload = get_tool_pricing_payload("generate_controller_brief")
        payload = _build_verify_failed_response(
            "generate_controller_brief",
            pricing_payload=pricing_payload,
            preferred_provider="coinbase",
            failure={"code": "verification_failed", "message": "signature rejected"},
        )

        self.assertEqual(payload["reason_code"], "verification_failed")
        self.assertEqual(payload["tool_or_endpoint"], "generate_controller_brief")
        self.assertIn("payment_provider_hint", payload)
        self.assertEqual(payload["payment_provider_hint"]["default"], "coinbase")
        self.assertEqual(payload["free_alternative"]["tool"], "quick_operational_recovery")
        self.assertIn("PAYMENT-SIGNATURE", payload["retry_example"])
        self.assertEqual(payload["docs_url"], "https://delx.ai/docs/x402-setup")

    async def test_x402_error_metrics_paginates_beyond_supabase_row_cap(self):
        store = SupabaseSessionStore()
        store._http = object()
        now = "2026-03-11T11:25:33.015732+00:00"

        first_page = [
            {
                "agent_id": "bulk-agent",
                "event_type": "x402_payment_required",
                "timestamp": now,
                "metadata": {"protocol": "rest", "tool_name": "generate_controller_brief", "provider": "unknown", "source": "prod-sdk"},
            }
            for _ in range(1000)
        ]
        second_page = [
            {
                "agent_id": "agent-real-1",
                "event_type": "x402_payment_verified",
                "timestamp": now,
                "metadata": {"protocol": "rest", "tool_name": "generate_controller_brief", "provider": "coinbase", "source": "prod-sdk"},
            },
            {
                "agent_id": "agent-real-2",
                "event_type": "x402_verify_failed",
                "timestamp": now,
                "metadata": {
                    "protocol": "mcp",
                    "method": "tools/call",
                    "provider": "coinbase",
                    "source": "prod-sdk",
                    "failure_code": "chain_mismatch",
                },
            },
        ]
        seen_offsets = []

        async def fake_get(path: str, *, params=None, prefer_count=False):
            self.assertEqual(path, "/rest/v1/events")
            self.assertEqual(params.get("event_type"), "like.x402_%")
            offset = int(params.get("offset", "0"))
            seen_offsets.append(offset)
            if offset == 0:
                return _FakeResponse(first_page)
            if offset == 1000:
                return _FakeResponse(second_page)
            return _FakeResponse([])

        store._get = fake_get  # type: ignore[method-assign]

        metrics = await store.get_x402_error_metrics(24)

        self.assertEqual(seen_offsets, [0, 1000])
        self.assertEqual(metrics["totals"]["events"], 1002)
        self.assertEqual(metrics["totals"]["payment_required"], 1000)
        self.assertEqual(metrics["totals"]["payment_verified"], 1)
        self.assertEqual(metrics["totals"]["verify_failed"], 1)
        self.assertEqual(metrics["by_failure_code"][0]["failure_code"], "chain_mismatch")
        self.assertEqual(metrics["by_failure_code"][0]["count"], 1)


if __name__ == "__main__":
    unittest.main()
