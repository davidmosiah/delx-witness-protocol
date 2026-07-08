import json
import sys
import unittest
from pathlib import Path

from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server as server_mod
import config as config_mod
from config import (
    X402_BAZAAR_METADATA,
    build_bazaar_tool_readiness,
    get_tool_bazaar_metadata,
    get_tool_pricing_payload,
    global_bazaar_listing_status,
    x402_provider_registry,
)
from x402_guard import _build_402_response

if config_mod.is_all_free_mode():
    raise unittest.SkipTest("Legacy x402 multi-provider contracts are retired in public-free therapy mode.")


class X402MultiProviderContractTests(unittest.TestCase):
    def setUp(self):
        self.original_id = config_mod.settings.COINBASE_CDP_API_KEY_ID
        self.original_secret = config_mod.settings.COINBASE_CDP_API_KEY_SECRET
        self.original_payai_accepts = getattr(config_mod.settings, "PAYAI_ACCEPTS_JSON", "")
        config_mod.settings.COINBASE_CDP_API_KEY_ID = "organizations/org-123/apiKeys/key-456"
        config_mod.settings.COINBASE_CDP_API_KEY_SECRET = "dGVzdA==" * 8
        config_mod.settings.PAYAI_ACCEPTS_JSON = ""

    def tearDown(self):
        config_mod.settings.COINBASE_CDP_API_KEY_ID = self.original_id
        config_mod.settings.COINBASE_CDP_API_KEY_SECRET = self.original_secret
        config_mod.settings.PAYAI_ACCEPTS_JSON = self.original_payai_accepts

    def test_pricing_payload_exposes_multiple_payment_providers_for_premium_tool(self):
        payload = get_tool_pricing_payload("generate_controller_brief")
        self.assertEqual(payload["default_payment_provider"], "coinbase")
        self.assertEqual(payload["payment_providers"], ["coinbase", "payai"])

    def test_pricing_payload_can_sell_recovery_plan_and_session_summary_for_one_cent(self):
        for tool_name in ("get_recovery_action_plan", "get_session_summary"):
            payload = get_tool_pricing_payload(tool_name)
            self.assertEqual(payload["price_cents"], 1)
            self.assertEqual(payload["price_usdc"], "0.01")
            self.assertTrue(payload["x402_required"])
            self.assertEqual(payload["default_payment_provider"], "coinbase")
            self.assertEqual(payload["payment_providers"], ["coinbase", "payai"])

    def test_402_response_advertises_multiple_accepts(self):
        pricing_payload = get_tool_pricing_payload("generate_controller_brief")
        resp = _build_402_response("generate_controller_brief", pricing_payload=pricing_payload)
        self.assertEqual(len(resp["accepts"]), 2)
        providers = [item["extra"]["provider"] for item in resp["accepts"]]
        self.assertEqual(providers, ["coinbase", "payai"])
        coinbase_requirement = next(item for item in resp["accepts"] if item["extra"]["provider"] == "coinbase")
        self.assertEqual(coinbase_requirement["network"], "eip155:8453")
        self.assertIn("extensions", resp)
        self.assertEqual(resp["extensions"]["bazaar"]["tool_name"], "generate_controller_brief")

    def test_402_response_supports_multi_provider_recovery_plan_with_bazaar_extension(self):
        pricing_payload = get_tool_pricing_payload("get_recovery_action_plan")
        resp = _build_402_response("get_recovery_action_plan", pricing_payload=pricing_payload)
        self.assertEqual(len(resp["accepts"]), 2)
        providers = [item["extra"]["provider"] for item in resp["accepts"]]
        self.assertEqual(providers, ["coinbase", "payai"])
        self.assertEqual(resp["resource"]["url"], "https://delx.ai/mcp/tools/get_recovery_action_plan")
        self.assertIn("extensions", resp)
        self.assertEqual(resp["extensions"]["bazaar"]["tool_name"], "get_recovery_action_plan")

    def test_registry_and_402_can_expose_payai_base_and_solana_together(self):
        config_mod.settings.PAYAI_ACCEPTS_JSON = json.dumps(
            [
                {
                    "network": "eip155:8453",
                    "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                    "pay_to": "0x9f8bd9875b3E0b632a24A3A7C73f7787175e73A2",
                    "label": "PayAI Base",
                },
                {
                    "network": "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1",
                    "asset": "USDC",
                    "pay_to": "9xQeWvG816bUx9EPjHmaT23yvVMN7T8Yv7c4Rk3x3G8R",
                    "label": "PayAI Solana",
                    "extra": {"feePayer": "So1FeePayer111111111111111111111111111111111"},
                },
            ]
        )

        registry = x402_provider_registry()
        self.assertEqual(len(registry["payai"]["accepts"]), 2)
        self.assertEqual(registry["payai"]["accepts"][1]["network"], "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1")

        pricing_payload = get_tool_pricing_payload("util_page_extract")
        resp = _build_402_response("util_page_extract", pricing_payload=pricing_payload)
        accepts = resp["accepts"]
        self.assertEqual(len(accepts), 3)
        self.assertEqual(
            [row["network"] for row in accepts],
            ["eip155:8453", "eip155:8453", "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"],
        )
        self.assertEqual(
            [row["extra"]["provider"] for row in accepts],
            ["coinbase", "payai", "payai"],
        )
        self.assertEqual(accepts[2]["extra"]["feePayer"], "So1FeePayer111111111111111111111111111111111")


class X402FacilitatorRequirementShapeTests(unittest.TestCase):
    def test_coinbase_facilitator_requirements_preserve_resource_metadata(self):
        from x402_guard import _build_payment_requirements, _build_facilitator_payment_requirements

        pricing_payload = get_tool_pricing_payload("generate_controller_brief")
        req = _build_payment_requirements(
            "generate_controller_brief",
            provider_name="coinbase",
            pricing_payload=pricing_payload,
        )
        facilitator_req = _build_facilitator_payment_requirements(req, provider_name="coinbase")

        self.assertEqual(facilitator_req["amount"], req["amount"])
        self.assertEqual(facilitator_req["network"], "eip155:8453")
        self.assertEqual(facilitator_req["payTo"], req["payTo"])
        self.assertEqual(facilitator_req["maxTimeoutSeconds"], req["maxTimeoutSeconds"])
        self.assertEqual(facilitator_req["resource"], req["resource"])
        self.assertEqual(facilitator_req["description"], req["description"])
        self.assertEqual(facilitator_req["mimeType"], req["mimeType"])
        self.assertEqual(facilitator_req["inputSchema"], req["inputSchema"])
        self.assertEqual(facilitator_req["outputSchema"], req["outputSchema"])
        self.assertEqual(facilitator_req["extra"], req["extra"])

    def test_non_coinbase_facilitator_requirements_remain_minimal(self):
        from x402_guard import _build_payment_requirements, _build_facilitator_payment_requirements

        pricing_payload = get_tool_pricing_payload("generate_controller_brief")
        req = _build_payment_requirements(
            "generate_controller_brief",
            provider_name="payai",
            pricing_payload=pricing_payload,
        )
        facilitator_req = _build_facilitator_payment_requirements(req, provider_name="payai")

        self.assertEqual(facilitator_req["amount"], req["amount"])
        self.assertEqual(facilitator_req["network"], req["network"])
        self.assertEqual(facilitator_req["payTo"], req["payTo"])
        self.assertEqual(facilitator_req["maxTimeoutSeconds"], req["maxTimeoutSeconds"])
        self.assertNotIn("maxAmountRequired", facilitator_req)
        self.assertNotIn("resource", facilitator_req)
        self.assertNotIn("description", facilitator_req)
        self.assertNotIn("mimeType", facilitator_req)


class BazaarToolReadinessTests(unittest.TestCase):
    def setUp(self):
        self.original_id = config_mod.settings.COINBASE_CDP_API_KEY_ID
        self.original_secret = config_mod.settings.COINBASE_CDP_API_KEY_SECRET
        config_mod.settings.COINBASE_CDP_API_KEY_ID = "organizations/org-123/apiKeys/key-456"
        config_mod.settings.COINBASE_CDP_API_KEY_SECRET = "dGVzdA==" * 8

    def tearDown(self):
        config_mod.settings.COINBASE_CDP_API_KEY_ID = self.original_id
        config_mod.settings.COINBASE_CDP_API_KEY_SECRET = self.original_secret

    def test_bazaar_tool_readiness_tracks_each_premium_tool_independently(self):
        rows = {
            row["tool_name"]: row
            for row in build_bazaar_tool_readiness(
                {
                    "get_recovery_action_plan": 1,
                    "get_session_summary": 1,
                    "generate_controller_brief": 12,
                    "generate_incident_rca": 2,
                    "generate_fleet_summary": 4,
                }
            )
        }

        self.assertEqual(set(rows.keys()), set(X402_BAZAAR_METADATA.keys()))
        self.assertEqual(rows["generate_controller_brief"]["listing_status"], "payment_verified_waiting_for_index")
        self.assertEqual(rows["generate_incident_rca"]["listing_status"], "payment_verified_waiting_for_index")
        self.assertEqual(rows["generate_fleet_summary"]["listing_status"], "payment_verified_waiting_for_index")
        self.assertEqual(rows["get_recovery_action_plan"]["listing_status"], "payment_verified_waiting_for_index")
        self.assertEqual(rows["get_session_summary"]["listing_status"], "payment_verified_waiting_for_index")
        self.assertEqual(rows["util_page_extract"]["listing_status"], "awaiting_first_coinbase_payment")
        self.assertEqual(rows["util_email_validate"]["listing_status"], "awaiting_first_coinbase_payment")

    def test_bazaar_tool_readiness_marks_all_premium_tools_discoverable(self):
        rows = {
            row["tool_name"]: row
            for row in build_bazaar_tool_readiness(
                {
                    "get_recovery_action_plan": 1,
                    "get_session_summary": 1,
                    "generate_controller_brief": 1,
                    "generate_incident_rca": 1,
                    "generate_fleet_summary": 1,
                }
            )
        }

        self.assertTrue(rows["generate_controller_brief"]["discoverable"])
        self.assertTrue(rows["generate_incident_rca"]["discoverable"])
        self.assertTrue(rows["generate_fleet_summary"]["discoverable"])
        self.assertTrue(rows["get_recovery_action_plan"]["discoverable"])
        self.assertTrue(rows["get_session_summary"]["discoverable"])

    def test_bazaar_tool_readiness_marks_publicly_indexed_tools(self):
        rows = {
            row["tool_name"]: row
            for row in build_bazaar_tool_readiness(
                {
                    "generate_controller_brief": 12,
                    "generate_incident_rca": 2,
                    "generate_fleet_summary": 4,
                },
                indexed_tools={"generate_controller_brief"},
            )
        }

        self.assertEqual(
            rows["generate_controller_brief"]["listing_status"],
            "indexed_in_coinbase_bazaar",
        )
        self.assertTrue(rows["generate_controller_brief"]["indexed_publicly"])
        self.assertEqual(
            rows["generate_incident_rca"]["listing_status"],
            "payment_verified_waiting_for_index",
        )
        self.assertFalse(rows["generate_incident_rca"]["indexed_publicly"])

    def test_global_bazaar_listing_status_reflects_partial_indexing(self):
        self.assertEqual(
            global_bazaar_listing_status(
                coinbase_verified_payments=3,
                indexed_tool_count=1,
                expected_tool_count=3,
            ),
            "partially_indexed_in_coinbase_bazaar",
        )

    def test_bazaar_metadata_can_reflect_public_indexing(self):
        bazaar = get_tool_bazaar_metadata(
            "generate_controller_brief",
            coinbase_verified_payments=3,
            indexed_publicly=True,
        )
        self.assertEqual(bazaar["listing_status"], "indexed_in_coinbase_bazaar")
        self.assertEqual(bazaar["listing_blockers"], [])

    def test_hero_tool_metadata_uses_explicit_discovery_copy(self):
        recovery = get_tool_bazaar_metadata("get_recovery_action_plan", coinbase_verified_payments=1)
        website = get_tool_bazaar_metadata("util_website_intelligence_report", coinbase_verified_payments=1)
        trust = get_tool_bazaar_metadata("util_domain_trust_report", coinbase_verified_payments=1)
        audit = get_tool_bazaar_metadata("util_x402_server_audit", coinbase_verified_payments=1)
        readiness = get_tool_bazaar_metadata("util_api_integration_readiness", coinbase_verified_payments=1)

        self.assertIn("controller-readable", recovery["summary"].lower())
        self.assertIn("stabilize", recovery["summary"].lower())
        self.assertEqual(recovery["tags"], ["mcp", "agent-ops", "incident-recovery", "loop", "controller"])
        self.assertIn("One-call website intelligence report", website["summary"])
        self.assertEqual(website["tags"], ["x402", "web", "research", "gtm", "crawl"])
        self.assertIn("vendor-risk", trust["tags"])
        self.assertIn("One-call domain trust", trust["summary"])
        self.assertIn("listing readiness", audit["summary"])
        self.assertEqual(audit["tags"], ["x402", "audit", "discovery", "pricing", "openapi"])
        self.assertIn("agent integration", readiness["summary"])
        self.assertIn("x402 signals", readiness["summary"])


class X402MonetizationPolicyContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_id = config_mod.settings.COINBASE_CDP_API_KEY_ID
        self.original_secret = config_mod.settings.COINBASE_CDP_API_KEY_SECRET
        self.original_payai_accepts = getattr(config_mod.settings, "PAYAI_ACCEPTS_JSON", "")
        config_mod.settings.COINBASE_CDP_API_KEY_ID = "organizations/org-123/apiKeys/key-456"
        config_mod.settings.COINBASE_CDP_API_KEY_SECRET = "dGVzdA==" * 8
        config_mod.settings.PAYAI_ACCEPTS_JSON = ""

    async def asyncTearDown(self):
        config_mod.settings.COINBASE_CDP_API_KEY_ID = self.original_id
        config_mod.settings.COINBASE_CDP_API_KEY_SECRET = self.original_secret
        config_mod.settings.PAYAI_ACCEPTS_JSON = self.original_payai_accepts

    async def test_monetization_policy_exposes_enabled_payment_providers(self):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/monetization-policy",
            "query_string": b"",
            "headers": [],
        }
        response = await server_mod.monetization_policy_endpoint(Request(scope))
        payload = json.loads(response.body)
        providers = payload["policy"]["payment_providers"]
        self.assertEqual(providers["default"], "coinbase")
        self.assertIn("coinbase", providers["enabled"])
        self.assertEqual(providers["tool_overrides"]["generate_controller_brief"], ["coinbase", "payai"])
        bazaar = payload["policy"]["bazaar"]
        self.assertFalse(bazaar["manual_registration_supported"])
        readiness = {row["tool_name"]: row for row in bazaar["tool_readiness"]}
        self.assertIn("generate_controller_brief", readiness)
        self.assertEqual(readiness["generate_controller_brief"]["listing_status"], "awaiting_first_coinbase_payment")

    async def test_well_known_can_publish_payai_solana_for_discovery(self):
        config_mod.settings.PAYAI_ACCEPTS_JSON = json.dumps(
            [
                {
                    "network": "eip155:8453",
                    "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                    "pay_to": "0x9f8bd9875b3E0b632a24A3A7C73f7787175e73A2",
                    "label": "PayAI Base",
                },
                {
                    "network": "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1",
                    "asset": "USDC",
                    "pay_to": "9xQeWvG816bUx9EPjHmaT23yvVMN7T8Yv7c4Rk3x3G8R",
                    "label": "PayAI Solana",
                },
            ]
        )

        payload = await server_mod._build_x402_well_known_payload()
        row = next(item for item in payload["resourceCatalog"] if item["tool_name"] == "util_page_extract")
        self.assertEqual(
            [entry["network"] for entry in row["accepts"]],
            ["eip155:8453", "eip155:8453", "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"],
        )
        self.assertEqual(row["supported_networks"], ["eip155:8453", "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"])
        self.assertEqual(payload["policy"]["supported_networks"], ["eip155:8453", "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"])
        self.assertEqual(len(payload["policy"]["provider_registry"]["payai"]["accepts"]), 2)


if __name__ == "__main__":
    unittest.main()
