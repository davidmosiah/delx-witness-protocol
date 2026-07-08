import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utility_metering import build_utility_adoption_snapshot, build_utility_metering_dashboard, classify_utility_event
from utility_registry import resolve_utility_tool_slug, utility_slug_for_tool


class UtilityMeteringQualityTest(unittest.TestCase):
    def test_x402_missing_input_is_payment_discovery_not_adoption(self):
        row = {
            "status": "missing_required_input",
            "route_type": "legacy_x402",
            "user_agent": "x402station/0.1 uptime-probe",
            "ok": 0,
        }

        self.assertEqual(classify_utility_event(row), "payment_discovery_probe")

    def test_valid_target_with_stable_agent_is_real_demand(self):
        row = {
            "status": "success",
            "route_type": "canonical",
            "ok": 1,
            "target_host": "delx.ai",
            "agent_id": "real-agent",
        }

        self.assertEqual(classify_utility_event(row), "valid_utility_user")

    def test_operator_smoke_is_not_counted_as_real_demand(self):
        row = {
            "status": "success",
            "route_type": "canonical",
            "ok": 1,
            "target_host": "delx.ai",
            "agent_id": "codex-live-smoke",
            "source": "codex-smoke",
        }

        self.assertEqual(classify_utility_event(row), "operator_test")

    def test_successful_crawler_call_is_discovery_not_real_demand(self):
        row = {
            "status": "success",
            "route_type": "canonical",
            "ok": 1,
            "target_host": "delx.ai",
            "user_agent": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.3)",
        }

        self.assertEqual(classify_utility_event(row), "crawler_discovery")

    def test_verified_payment_is_paid_utility_demand(self):
        row = {
            "status": "success",
            "route_type": "canonical",
            "ok": 1,
            "target_host": "delx.ai",
            "user_agent": "node",
            "enforced_revenue_usdc": 0.01,
        }

        self.assertEqual(classify_utility_event(row), "paid_verified_utility_user")

    def test_dashboard_exposes_demand_quality_totals(self):
        rows = [
            {
                "product_id": "domain_trust_report",
                "tool_name": "util_domain_trust_report",
                "status": "success",
                "route_type": "canonical",
                "ok": 1,
                "target_host": "delx.ai",
                "agent_id": "real-agent",
                "latency_ms": 12,
            },
            {
                "product_id": "domain_trust_report",
                "tool_name": "util_domain_trust_report",
                "status": "missing_required_input",
                "route_type": "legacy_x402",
                "ok": 0,
                "user_agent": "x402station/0.1 uptime-probe",
            },
            {
                "product_id": "domain_trust_report",
                "tool_name": "util_domain_trust_report",
                "status": "success",
                "route_type": "canonical",
                "ok": 1,
                "target_host": "delx.ai",
                "agent_id": "codex-live-smoke",
            },
            {
                "product_id": "domain_trust_report",
                "tool_name": "util_domain_trust_report",
                "status": "success",
                "route_type": "canonical",
                "ok": 1,
                "target_host": "delx.ai",
                "user_agent": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0)",
            },
            {
                "product_id": "domain_trust_report",
                "tool_name": "util_domain_trust_report",
                "status": "success",
                "route_type": "canonical",
                "ok": 1,
                "target_host": "delx.ai",
                "user_agent": "node",
                "enforced_revenue_usdc": 0.01,
            },
        ]

        dashboard = build_utility_metering_dashboard(
            rows,
            product_catalog={"products": [], "monetization_rollout": {"charge_mode": "enforce"}},
            days=1,
        )

        self.assertEqual(dashboard["totals"]["real_demand_calls"], 2)
        self.assertEqual(dashboard["totals"]["paid_verified_utility_user_calls"], 1)
        self.assertEqual(dashboard["totals"]["crawler_discovery_calls"], 1)
        self.assertEqual(dashboard["totals"]["payment_discovery_probe_calls"], 1)
        self.assertEqual(dashboard["totals"]["operator_test_calls"], 1)
        self.assertEqual(dashboard["totals"]["enforced_revenue_usdc"], 0.01)
        self.assertEqual(dashboard["by_product"][0]["valid_demand_calls"], 2)
        self.assertEqual(dashboard["by_product"][0]["probe_calls"], 2)
        self.assertEqual(dashboard["pricing"]["rollout"], "enforce")

    def test_adoption_snapshot_separates_real_demand_from_probe_noise(self):
        rows = [
            {
                "product_id": "domain_trust_report",
                "tool_name": "util_domain_trust_report",
                "status": "success",
                "route_type": "canonical",
                "ok": 1,
                "target_host": "delx.ai",
                "agent_id": "new-real-agent",
                "transport": "mcp",
                "source": "smithery",
                "latency_ms": 20,
                "shadow_revenue_usdc": 0.01,
            },
            {
                "product_id": "domain_trust_report",
                "tool_name": "util_domain_trust_report",
                "status": "missing_required_input",
                "route_type": "legacy_x402",
                "ok": 0,
                "transport": "rest.x402",
                "user_agent": "x402station/0.1 uptime-probe",
            },
        ]

        snapshot = build_utility_adoption_snapshot(
            rows,
            product_catalog={"products": [{"product_id": "domain_trust_report"}]},
            window_hours=12,
            prior_agents=set(),
        )

        self.assertEqual(snapshot["status"], "real_demand")
        self.assertEqual(snapshot["totals"]["real_demand_calls"], 1)
        self.assertEqual(snapshot["totals"]["probe_calls"], 1)
        self.assertEqual(snapshot["totals"]["new_agents"], 1)
        self.assertEqual(snapshot["totals"]["shadow_revenue_usdc"], 0.01)
        self.assertEqual(snapshot["cost_guardrail"]["llm_calls_expected"], 0)

    def test_utility_registry_resolves_canonical_and_compat_slugs(self):
        self.assertEqual(utility_slug_for_tool("util_uuid_generate"), "uuid")
        self.assertEqual(resolve_utility_tool_slug("uuid-generate"), "util_uuid_generate")
        self.assertEqual(resolve_utility_tool_slug("x402-server-audit"), "util_x402_server_audit")


if __name__ == "__main__":
    unittest.main()
