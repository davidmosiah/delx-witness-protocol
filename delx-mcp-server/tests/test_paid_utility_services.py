import json
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from mcp.types import TextContent
from starlette.requests import Request
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server as server_mod
import util_tools
import config as config_mod
from delx_agent_utilities._internal import _tools_web
from delx_agent_utilities._internal._mcp_readiness import build_mcp_server_readiness_report
from utility_monetization import get_metered_utility_pricing_payload
from x402_guard import X402Middleware, _build_402_response, _build_facilitator_payment_requirements


@contextmanager
def paid_utility_rollout(mode: str = "enforce", tools: str | None = None):
    original_all_free = server_mod.settings.MONETIZATION_ALL_FREE
    original_mode = server_mod.settings.MONETIZATION_UTILITY_CHARGE_MODE
    original_tools = server_mod.settings.MONETIZATION_UTILITY_CHARGE_TOOLS
    try:
        server_mod.settings.MONETIZATION_ALL_FREE = False
        server_mod.settings.MONETIZATION_UTILITY_CHARGE_MODE = mode
        if tools is not None:
            server_mod.settings.MONETIZATION_UTILITY_CHARGE_TOOLS = tools
        yield
    finally:
        server_mod.settings.MONETIZATION_ALL_FREE = original_all_free
        server_mod.settings.MONETIZATION_UTILITY_CHARGE_MODE = original_mode
        server_mod.settings.MONETIZATION_UTILITY_CHARGE_TOOLS = original_tools


class FoundingAccessUtilityTests(unittest.TestCase):
    def test_metered_pricing_payload_respects_current_all_free_mode(self):
        original_all_free = server_mod.settings.MONETIZATION_ALL_FREE
        original_mode = server_mod.settings.MONETIZATION_UTILITY_CHARGE_MODE
        try:
            server_mod.settings.MONETIZATION_ALL_FREE = True
            server_mod.settings.MONETIZATION_UTILITY_CHARGE_MODE = "enforce"

            pricing = get_metered_utility_pricing_payload("util_domain_trust_report")
        finally:
            server_mod.settings.MONETIZATION_ALL_FREE = original_all_free
            server_mod.settings.MONETIZATION_UTILITY_CHARGE_MODE = original_mode

        self.assertEqual(pricing["price_cents"], 0)
        self.assertEqual(pricing["price_usdc"], "0.00")
        self.assertFalse(pricing["x402_required"])
        self.assertTrue(pricing["all_free_mode"])
        self.assertEqual(pricing["future_price_cents"], 1)
        self.assertEqual(pricing["future_price_usdc"], "0.01")

    def test_all_free_mode_overrides_enforced_utility_charge(self):
        client = TestClient(server_mod.app)
        original_all_free = server_mod.settings.MONETIZATION_ALL_FREE
        original_mode = server_mod.settings.MONETIZATION_UTILITY_CHARGE_MODE
        original_tools = server_mod.settings.MONETIZATION_UTILITY_CHARGE_TOOLS
        try:
            server_mod.settings.MONETIZATION_ALL_FREE = True
            server_mod.settings.MONETIZATION_UTILITY_CHARGE_MODE = "enforce"
            server_mod.settings.MONETIZATION_UTILITY_CHARGE_TOOLS = "util_domain_trust_report"

            catalog = client.get("/api/v1/utilities/catalog")
            with patch.object(
                server_mod,
                "call_util_tool",
                new=AsyncMock(return_value={"url": "https://delx.ai", "domain": "delx.ai", "trust_score": 90}),
            ):
                response = client.post(
                    "/api/v1/x402/domain-trust-report",
                    json={"url": "https://delx.ai", "timeout": 8},
                )
        finally:
            server_mod.settings.MONETIZATION_ALL_FREE = original_all_free
            server_mod.settings.MONETIZATION_UTILITY_CHARGE_MODE = original_mode
            server_mod.settings.MONETIZATION_UTILITY_CHARGE_TOOLS = original_tools

        self.assertEqual(catalog.status_code, 200)
        product = next(row for row in catalog.json()["products"] if row["product_id"] == "domain_trust_report")
        self.assertEqual(product["price"]["amount"], "0.00")
        self.assertEqual(product["price"]["future_amount"], "0.01")
        self.assertTrue(product["price"]["free_access"])
        self.assertEqual(product["monetization"]["charge_mode"], "off")
        self.assertFalse(product["monetization"]["charge_enabled"])

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["monetization"]["price_usdc"], "0.00")
        self.assertEqual(payload["monetization"]["mode"], "off")


class UtilitySurfaceTests(unittest.IsolatedAsyncioTestCase):
    def test_public_utils_routes_are_no_longer_registered(self):
        client = TestClient(server_mod._starlette_app)

        self.assertEqual(client.get("/api/v1/utils").status_code, 404)
        self.assertEqual(client.get("/api/v1/utils/email-validate").status_code, 404)

    async def test_tools_catalog_all_lists_utility_tools_but_core_does_not(self):
        response = await server_mod.tools_catalog(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/v1/tools",
                    "query_string": b"",
                    "headers": [],
                }
            )
        )
        payload = json.loads(response.body)
        names = {tool["canonical_name"] for tool in payload["tools"]}

        self.assertIn("util_uuid_generate", names)

        core_response = await server_mod.tools_catalog(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/v1/tools",
                    "query_string": b"tier=core",
                    "headers": [],
                }
            )
        )
        core_payload = json.loads(core_response.body)
        core_names = {tool["canonical_name"] for tool in core_payload["tools"]}
        self.assertTrue(core_names)
        self.assertTrue(all(not name.startswith("util_") for name in core_names))

    async def test_calling_utility_tool_over_mcp_returns_result(self):
        result = await server_mod.call_tool(
            "util_uuid_generate",
            {},
        )

        payload = json.loads(result[0].text)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["surface"], "delx-agent-utilities")
        self.assertEqual(payload["tool_name"], "util_uuid_generate")
        self.assertIn("uuids", payload["result"])

    async def test_productized_utility_over_mcp_includes_commerce_metadata(self):
        with paid_utility_rollout("enforce"):
            with patch.object(
                server_mod,
                "call_util_tool",
                new=AsyncMock(return_value={"url": "https://delx.ai", "domain": "delx.ai", "trust_score": 90, "trust_level": "high"}),
            ):
                result = await server_mod.call_tool(
                    "util_domain_trust_report",
                    {"host": "delx.ai"},
                )

        payload = json.loads(result[0].text)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["product"]["product_id"], "domain_trust_report")
        self.assertEqual(payload["agent_report"]["product_id"], "domain_trust_report")
        self.assertEqual(payload["monetization"]["mode"], "enforce")
        self.assertEqual(payload["monetization"]["price_usdc"], "0.01")
        self.assertIn("accepted alias host as url; prefer url", payload["warnings"])

    async def test_mcp_utility_missing_params_are_top_level_contract(self):
        result = await server_mod.call_tool("util_url_health", {})

        payload = json.loads(result[0].text)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "DELX-UTIL-1001")
        self.assertEqual(payload["error"], "missing_required_params")
        self.assertEqual(payload["missing"], ["url"])
        self.assertEqual(payload["required"], ["url"])
        self.assertEqual(payload["schema_url"], "https://api.delx.ai/api/v1/tools/schema/util_url_health")

    async def test_mcp_tools_list_supports_utilities_tier_and_surface_role(self):
        response = await server_mod.handle_mcp_rpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {"format": "compact", "tier": "utilities"},
            }
        )

        result = response["result"]
        self.assertEqual(result["tier"], "utilities")
        self.assertEqual(result["count"], 41)
        tools = result["tools"]
        self.assertTrue(all(tool["canonical_name"].startswith("util_") for tool in tools))
        domain = next(tool for tool in tools if tool["canonical_name"] == "util_domain_trust_report")
        self.assertEqual(domain["surface_role"], "agent_utility")
        self.assertEqual(domain["product"]["product_id"], "domain_trust_report")
        self.assertEqual(domain["monetization"]["price_usdc"], "0.00")
        mcp_readiness = next(tool for tool in tools if tool["canonical_name"] == "util_mcp_server_readiness_report")
        self.assertEqual(mcp_readiness["surface_role"], "agent_utility")
        self.assertEqual(mcp_readiness["product"]["product_id"], "mcp_server_readiness_report")
        self.assertEqual(mcp_readiness["monetization"]["price_usdc"], "0.00")

    async def test_mcp_full_discovery_includes_utility_metadata(self):
        response = await server_mod.handle_mcp_rpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {"format": "full", "tier": "utilities"},
            }
        )

        tools = response["result"]["tools"]
        domain = next(tool for tool in tools if tool["canonical_name"] == "util_domain_trust_report")
        self.assertEqual(domain["surface_role"], "agent_utility")
        self.assertEqual(domain["canonical_endpoint"], "https://api.delx.ai/api/v1/utilities/domain-trust-report")
        self.assertEqual(domain["x402_endpoint"], "https://api.delx.ai/api/v1/x402/domain-trust-report")
        self.assertEqual(domain["product"]["product_id"], "domain_trust_report")

    async def test_safe_fetch_blocks_private_and_link_local_targets(self):
        private_payload = await util_tools.call_util_tool("util_page_extract", {"url": "http://127.0.0.1:80"})
        metadata_payload = await util_tools.call_util_tool("util_url_health", {"url": "http://169.254.169.254/latest/meta-data/"})
        file_payload = await util_tools.call_util_tool("util_page_extract", {"url": "file:///etc/passwd"})

        self.assertFalse(private_payload["reachable"])
        self.assertIn("blocked", private_payload["error"])
        self.assertFalse(metadata_payload["reachable"])
        self.assertIn("blocked", metadata_payload["reason"])
        self.assertFalse(file_payload["reachable"])
        self.assertIn("http and https", file_payload["error"])

    async def test_url_health_rejects_non_http_protocol_without_normalizing(self):
        payload = await util_tools.call_util_tool("util_url_health", {"url": "file:///etc/passwd"})

        self.assertFalse(payload["reachable"])
        self.assertEqual(payload["url"], "file:///etc/passwd")
        self.assertEqual(payload["code"], "invalid_protocol")

    async def test_x402_resource_summary_is_agent_readable_when_discovery_is_absent(self):
        class FakeResponse:
            status_code = 404

        with patch.object(
            _tools_web,
            "_fetch_json_response",
            new=AsyncMock(return_value=(FakeResponse(), None, "invalid json response: not json")),
        ):
            payload = await util_tools.call_util_tool("util_x402_resource_summary", {"url": "https://example.com"})

        self.assertEqual(payload["resource_count"], 0)
        self.assertIn("warning", payload)

    async def test_openapi_spec_exposes_productized_utility_routes_for_agentcash(self):
        with paid_utility_rollout(
            "enforce",
            (
                "util_website_intelligence_report,util_domain_trust_report,"
                "util_api_integration_readiness,util_x402_server_audit,util_company_contact_pack"
            ),
        ):
            payload = await server_mod._build_openapi_spec_payload()
            paid_only = await server_mod._build_openapi_spec_payload(paid_only=True)

        self.assertNotIn("/api/v1/previews/x402-server-audit", payload["paths"])
        self.assertIn("/api/v1/utilities/domain-trust-report", payload["paths"])
        self.assertIn("/api/v1/x402/domain-trust-report", payload["paths"])
        self.assertIn("/api/v1/x402/domain-trust-report", paid_only["paths"])

        operation = payload["paths"]["/api/v1/utilities/domain-trust-report"]["post"]
        self.assertEqual(operation["x-discovery"]["authMode"], "paid")
        self.assertEqual(operation["x-discovery"]["surfaceRole"], "agent_utility")
        self.assertEqual(operation["x-discovery"]["price"]["amount"], "0.01")
        self.assertIn("x402", operation["x-discovery"]["protocols"])
        self.assertEqual(operation["x-payment-info"]["pricingMode"], "fixed")
        self.assertEqual(operation["x-payment-info"]["price"], "0.01")
        self.assertIn("x402", operation["x-payment-info"]["protocols"])
        self.assertIn("x402PaymentSignature", payload["components"]["securitySchemes"])

    async def test_circle_gateway_nanopayments_can_be_advertised_for_metered_utilities(self):
        originals = {
            "CIRCLE_GATEWAY_NANOPAYMENTS_ENABLED": config_mod.settings.CIRCLE_GATEWAY_NANOPAYMENTS_ENABLED,
            "FACILITATOR_URL_CIRCLE_GATEWAY": config_mod.settings.FACILITATOR_URL_CIRCLE_GATEWAY,
            "CIRCLE_GATEWAY_VERIFYING_CONTRACT": config_mod.settings.CIRCLE_GATEWAY_VERIFYING_CONTRACT,
            "CIRCLE_GATEWAY_PAY_TO": config_mod.settings.CIRCLE_GATEWAY_PAY_TO,
            "MONETIZATION_UTILITY_CHARGE_MODE": config_mod.settings.MONETIZATION_UTILITY_CHARGE_MODE,
        }
        try:
            config_mod.settings.CIRCLE_GATEWAY_NANOPAYMENTS_ENABLED = True
            config_mod.settings.FACILITATOR_URL_CIRCLE_GATEWAY = "https://gateway-facilitator.example"
            config_mod.settings.CIRCLE_GATEWAY_VERIFYING_CONTRACT = "0x77777777Dcc4d5A8B6E418Fd04D8997ef11000eE"
            config_mod.settings.CIRCLE_GATEWAY_PAY_TO = "0x9f8bd9875b3E0b632a24A3A7C73f7787175e73A2"
            with paid_utility_rollout("enforce", "util_domain_trust_report"):
                pricing = get_metered_utility_pricing_payload("util_domain_trust_report")
                payload = _build_402_response(
                    "util_domain_trust_report",
                    pricing_payload=pricing,
                    resource="https://api.delx.ai/api/v1/utilities/domain-trust-report",
                )
        finally:
            for key, value in originals.items():
                setattr(config_mod.settings, key, value)

        circle = next(row for row in payload["accepts"] if row["extra"]["provider"] == "circle_gateway")
        self.assertEqual(circle["extra"]["name"], "GatewayWalletBatched")
        self.assertEqual(circle["extra"]["version"], "1")
        self.assertEqual(circle["extra"]["verifyingContract"], "0x77777777Dcc4d5A8B6E418Fd04D8997ef11000eE")
        self.assertEqual(circle["extra"]["chainId"], 8453)
        self.assertEqual(circle["extra"]["gatewayDomain"], 6)
        self.assertEqual(circle["extra"]["minValiditySeconds"], 604800)
        self.assertEqual(circle["maxTimeoutSeconds"], 604800)
        self.assertEqual(circle["maxAmountRequired"], "10000")
        self.assertIn("Circle Gateway nanopayments", " ".join(payload["next_steps"]))

        facilitator_req = _build_facilitator_payment_requirements(circle, provider_name="circle_gateway")
        self.assertEqual(facilitator_req["extra"]["verifyingContract"], "0x77777777Dcc4d5A8B6E418Fd04D8997ef11000eE")
        self.assertEqual(facilitator_req["extra"]["name"], "GatewayWalletBatched")
        self.assertEqual(facilitator_req["extra"]["minValiditySeconds"], 604800)
        self.assertEqual(facilitator_req["maxTimeoutSeconds"], 604800)

    async def test_circle_gateway_verify_failure_preserves_facilitator_reason(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/verify"):
                return httpx.Response(
                    200,
                    json={
                        "isValid": False,
                        "invalidReason": "authorization_validity_too_short",
                        "payer": "0x2a3945afc7dd6cf1ea2aa63122e01a68df55c941",
                    },
                )
            raise AssertionError(f"unexpected request path: {request.url.path}")

        provider_config = {
            "facilitator_url": "https://gateway-api.circle.com/v1/x402",
            "network": "eip155:8453",
            "asset": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "pay_to": "0x9f8bd9875b3E0b632a24A3A7C73f7787175e73A2",
            "label": "Circle Gateway Nanopayments",
        }
        provider_accept = {
            "network": provider_config["network"],
            "asset": provider_config["asset"],
            "pay_to": provider_config["pay_to"],
            "extra": {
                "name": "GatewayWalletBatched",
                "version": "1",
                "minValiditySeconds": 604800,
            },
        }
        middleware = X402Middleware(app=None, store=None, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        payment_header = json.dumps({"x402Version": 2, "payload": {"authorization": {}, "signature": "0xsignature"}})

        try:
            with patch("x402_guard._provider_order", return_value=["circle_gateway"]), patch(
                "x402_guard._provider_config",
                return_value=provider_config,
            ), patch("x402_guard._provider_requirement_candidates", return_value=[("circle_gateway", provider_accept)]):
                tx_hash, provider_name, failure = await middleware._verify_and_settle_payment(
                    payment_header,
                    "util_domain_trust_report",
                    get_metered_utility_pricing_payload("util_domain_trust_report"),
                    preferred_provider="circle_gateway",
                )
        finally:
            await middleware.http.aclose()

        self.assertIsNone(tx_hash)
        self.assertIsNone(provider_name)
        self.assertEqual(failure["code"], "authorization_validity_too_short")
        self.assertIn("authorization_validity_too_short", failure["message"])
        self.assertEqual(failure["provider_attempts"][0]["reason"], "authorization_validity_too_short")
        self.assertEqual(failure["provider_attempts"][0]["facilitator_response"]["invalidReason"], "authorization_validity_too_short")
        self.assertEqual(failure["provider_attempts"][0]["facilitator_response"]["payer"], "0x2a3945afc7dd6cf1ea2aa63122e01a68df55c941")

    def test_utility_402_verify_failure_exposes_circle_diagnostics(self):
        client = TestClient(server_mod.app)

        async def fake_verify(*args, **kwargs):
            return None, None, {
                "code": "insufficient_balance",
                "message": "Circle Gateway rejected the payment: insufficient_balance.",
                "provider_attempts": [
                    {
                        "provider": "circle_gateway",
                        "network": "eip155:8453",
                        "stage": "settle",
                        "status_code": 200,
                        "reason": "insufficient_balance",
                        "facilitator_response": {
                            "errorReason": "insufficient_balance",
                            "payer": "0x2a3945afc7dd6cf1ea2aa63122e01a68df55c941",
                            "network": "eip155:8453",
                        },
                    }
                ],
            }

        with paid_utility_rollout("enforce", "util_domain_trust_report"):
            with patch("x402_guard.X402Middleware._verify_and_settle_payment", new=fake_verify), patch.object(
                server_mod,
                "call_util_tool",
                new=AsyncMock(),
            ) as call_mock:
                response = client.post(
                    "/api/v1/x402/domain-trust-report",
                    json={"url": "https://delx.ai", "timeout": 8},
                    headers={"payment-signature": "synthetic", "x-payment-provider": "circle_gateway"},
                )

        self.assertEqual(response.status_code, 402)
        payload = response.json()
        self.assertEqual(payload["extensions"]["delx"]["failure_code"], "insufficient_balance")
        self.assertIn("insufficient_balance", payload["extensions"]["delx"]["failure_message"])
        self.assertEqual(payload["extensions"]["delx"]["provider_attempts"][0]["reason"], "insufficient_balance")
        self.assertEqual(payload["extensions"]["delx"]["payment_diagnostics"]["primary_reason"], "insufficient_balance")
        call_mock.assert_not_called()

    async def test_circle_gateway_requires_explicit_runtime_configuration_before_active_accepts(self):
        originals = {
            "CIRCLE_GATEWAY_NANOPAYMENTS_ENABLED": config_mod.settings.CIRCLE_GATEWAY_NANOPAYMENTS_ENABLED,
            "FACILITATOR_URL_CIRCLE_GATEWAY": config_mod.settings.FACILITATOR_URL_CIRCLE_GATEWAY,
            "CIRCLE_GATEWAY_VERIFYING_CONTRACT": config_mod.settings.CIRCLE_GATEWAY_VERIFYING_CONTRACT,
            "CIRCLE_GATEWAY_PAY_TO": config_mod.settings.CIRCLE_GATEWAY_PAY_TO,
            "MONETIZATION_UTILITY_CHARGE_MODE": config_mod.settings.MONETIZATION_UTILITY_CHARGE_MODE,
        }
        try:
            config_mod.settings.CIRCLE_GATEWAY_NANOPAYMENTS_ENABLED = True
            config_mod.settings.FACILITATOR_URL_CIRCLE_GATEWAY = ""
            config_mod.settings.CIRCLE_GATEWAY_PAY_TO = ""
            config_mod.settings.CIRCLE_GATEWAY_VERIFYING_CONTRACT = "0x77777777Dcc4d5A8B6E418Fd04D8997ef11000eE"
            config_mod.settings.MONETIZATION_UTILITY_CHARGE_MODE = "enforce"

            registry = config_mod.x402_provider_registry()
            self.assertFalse(registry["circle_gateway"]["enabled"])
            self.assertEqual(registry["circle_gateway"]["status"], "configuration_required")
            self.assertIn("FACILITATOR_URL_CIRCLE_GATEWAY", registry["circle_gateway"]["readiness"]["missing_env"])
            self.assertIn("CIRCLE_GATEWAY_PAY_TO", registry["circle_gateway"]["readiness"]["missing_env"])
            policy = config_mod.monetization_policy()
            policy_circle = policy["payment_providers"]["registry"]["circle_gateway"]
            self.assertFalse(policy_circle["enabled"])
            self.assertEqual(policy_circle["status"], "configuration_required")
            self.assertIn("FACILITATOR_URL_CIRCLE_GATEWAY", policy_circle["readiness"]["missing_env"])

            pricing = get_metered_utility_pricing_payload("util_domain_trust_report")
            payload = _build_402_response(
                "util_domain_trust_report",
                pricing_payload=pricing,
                resource="https://api.delx.ai/api/v1/utilities/domain-trust-report",
            )
        finally:
            for key, value in originals.items():
                setattr(config_mod.settings, key, value)

        providers = [row["extra"]["provider"] for row in payload["accepts"]]
        self.assertNotIn("circle_gateway", providers)

    async def test_well_known_x402_preserves_catalog_metadata_in_all_free_mode(self):
        with paid_utility_rollout("enforce"):
            payload = await server_mod._build_x402_well_known_payload()

        self.assertEqual(payload["policy"]["protocol_access_mode"], "public_free")
        self.assertEqual(payload["policy"]["utility_charge_mode"], "enforce")
        self.assertIn("https://api.delx.ai/api/v1/x402/domain-trust-report", payload["resources"])
        domain = next(row for row in payload["resourceCatalog"] if row["tool_name"] == "util_domain_trust_report")
        self.assertEqual(domain["resource"], "https://api.delx.ai/api/v1/x402/domain-trust-report")
        self.assertGreaterEqual(len(domain["accepts"]), 1)

    async def test_mcp_server_readiness_report_is_deterministic_and_decision_ready(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/mcp":
                body = json.loads(request.content.decode("utf-8"))
                if body["method"] == "initialize":
                    return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-06-18"}})
                if body["method"] == "tools/list":
                    return httpx.Response(
                        200,
                        json={
                            "jsonrpc": "2.0",
                            "id": body["id"],
                            "result": {
                                "tools": [
                                    {
                                        "name": "search_docs",
                                        "description": "Search docs.",
                                        "inputSchema": {
                                            "type": "object",
                                            "properties": {"query": {"type": "string", "description": "Search query"}},
                                            "required": ["query"],
                                        },
                                    },
                                    {
                                        "name": "bad tool",
                                        "description": "",
                                        "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}},
                                    },
                                ]
                            },
                        },
                    )
            if request.url.path == "/.well-known/mcp.json":
                return httpx.Response(200, json={"name": "Example MCP", "description": "Example server"})
            return httpx.Response(404, json={"error": "not found"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            report = await build_mcp_server_readiness_report(
                {"url": "https://mcp.example", "timeout": 3},
                http_client=client,
            )

        self.assertEqual(report["tool_name"], "util_mcp_server_readiness_report")
        self.assertEqual(report["surface"], "delx-agent-utilities")
        self.assertEqual(report["verdict"], "review_before_use")
        self.assertEqual(report["mcp_readiness_score"], 70)
        self.assertEqual(report["checks"]["mcp_initialize"]["ok"], True)
        self.assertEqual(report["checks"]["tools_list"]["tool_count"], 2)
        self.assertEqual(report["schema_quality"]["tools_with_descriptions"], 1)
        self.assertEqual(report["schema_quality"]["tools_with_arg_descriptions"], 1)
        self.assertIn("bad tool", report["issues"][0]["detail"])
        self.assertIn("Fix schema/name hygiene", report["next_action"])

    async def test_mcp_server_readiness_report_is_registered_as_paid_product(self):
        self.assertIn("util_mcp_server_readiness_report", util_tools.UTIL_TOOL_NAMES)
        self.assertEqual(util_tools.UTIL_REQUIRED_PARAMS["util_mcp_server_readiness_report"], ["url"])
        self.assertEqual(config_mod.PRICING["util_mcp_server_readiness_report"], 5)

        with paid_utility_rollout("enforce", "util_mcp_server_readiness_report"):
            pricing = get_metered_utility_pricing_payload("util_mcp_server_readiness_report")
            payload = _build_402_response(
                "util_mcp_server_readiness_report",
                pricing_payload=pricing,
                resource="https://api.delx.ai/api/v1/x402/mcp-server-readiness",
            )

        self.assertEqual(payload["accepts"][0]["amount"], "50000")
        self.assertEqual(payload["accepts"][0]["maxAmountRequired"], "50000")
        self.assertIn("mcp-server-readiness", payload["accepts"][0]["resource"])

    async def test_api_integration_readiness_report_is_deterministic_and_decision_ready(self):
        async def fake_health(args):
            return {"reachable": True, "status": 200, "latency_ms": 41, "content_type": "text/html"}

        async def fake_headers(args):
            return {
                "security_headers_present": ["strict-transport-security", "x-content-type-options"],
                "missing_security_headers": [],
            }

        async def fake_openapi(args):
            return {
                "reachable": True,
                "url": "https://api.example.com/openapi.json",
                "title": "Example API",
                "version": "1.0.0",
                "path_count": 6,
                "auth_hints": ["bearer", "api key"],
                "sample_paths": ["/v1/widgets", "/v1/users"],
            }

        async def fake_page(args):
            return {
                "reachable": True,
                "title": "Example API docs",
                "description": "Bearer auth, SDK quickstart, and rate limit guidance.",
                "text_excerpt": "Install the Python SDK, use Bearer auth, and respect documented rate limits.",
            }

        async def fake_links(args):
            return {
                "links": [
                    {"url": "https://api.example.com/docs", "kind": "internal"},
                    {"url": "https://api.example.com/openapi.json", "kind": "internal"},
                    {"url": "https://github.com/example/sdk-python", "kind": "external"},
                    {"url": "https://api.example.com/docs/quickstart", "kind": "internal"},
                ]
            }

        with (
            patch.object(_tools_web, "_api_health_report", new=fake_health),
            patch.object(_tools_web, "_http_headers_inspect", new=fake_headers),
            patch.object(_tools_web, "_openapi_summary", new=fake_openapi),
            patch.object(_tools_web, "_page_extract", new=fake_page),
            patch.object(_tools_web, "_links_extract", new=fake_links),
        ):
            report = await util_tools.call_util_tool(
                "util_api_integration_readiness",
                {"url": "https://api.example.com/docs", "timeout": 8},
            )

        self.assertEqual(report["tool_name"], "util_api_integration_readiness")
        self.assertEqual(report["surface"], "delx-agent-utilities")
        self.assertEqual(report["verdict"], "ready")
        self.assertGreaterEqual(report["api_readiness_score"], 85)
        self.assertEqual(report["readiness_level"], "high")
        self.assertEqual(report["auth"]["classification"], "bearer_or_api_key_detected")
        self.assertTrue(report["docs"]["openapi"]["found"])
        self.assertIn("https://github.com/example/sdk-python", report["docs"]["sdk_links"])
        self.assertNotIn("missing_rate_limit_docs", report["blockers"])
        self.assertIn("generate", report["agent_next_action"].lower())
        self.assertTrue(report["deterministic"])
        self.assertFalse(report["llm_used"])

    async def test_api_integration_readiness_is_registered_as_five_cent_paid_product(self):
        self.assertIn("util_api_integration_readiness", util_tools.UTIL_TOOL_NAMES)
        self.assertEqual(util_tools.UTIL_REQUIRED_PARAMS["util_api_integration_readiness"], ["url"])
        self.assertEqual(config_mod.PRICING["util_api_integration_readiness"], 5)

        with paid_utility_rollout("enforce", "util_api_integration_readiness"):
            pricing = get_metered_utility_pricing_payload("util_api_integration_readiness")
            payload = _build_402_response(
                "util_api_integration_readiness",
                pricing_payload=pricing,
                resource="https://api.delx.ai/api/v1/x402/api-integration-readiness",
            )

        self.assertEqual(payload["accepts"][0]["amount"], "50000")
        self.assertEqual(payload["accepts"][0]["maxAmountRequired"], "50000")
        self.assertIn("api-integration-readiness", payload["accepts"][0]["resource"])


class LegacyRedirectRouteTests(unittest.TestCase):
    def test_legacy_premium_missing_input_returns_agent_readable_422_without_tool_call(self):
        client = TestClient(server_mod.app)

        with patch.object(server_mod, "call_tool", new=AsyncMock()) as call_mock:
            response = client.get(
                "/api/v1/premium/recovery-action-plan?session_id=123e4567-e89b-12d3-a456-426614174000",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 422)
        call_mock.assert_not_awaited()
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "DELX-PREMIUM-1001")
        self.assertEqual(payload["status"], "legacy_compat_missing_input")
        self.assertEqual(payload["error"], "missing_required_params")
        self.assertEqual(payload["tool_name"], "get_recovery_action_plan")
        self.assertEqual(payload["missing"], ["incident_summary"])
        self.assertEqual(payload["required"], ["session_id", "incident_summary"])
        self.assertEqual(payload["schema_url"], "https://api.delx.ai/api/v1/tools/schema/get_recovery_action_plan")
        self.assertEqual(payload["canonical_endpoint"], "https://api.delx.ai/api/v1/premium/recovery-action-plan")
        self.assertIn("mcp_example", payload)
        self.assertEqual(payload["mcp_example"]["params"]["name"], "get_recovery_action_plan")
        self.assertEqual(payload["mcp_example"]["params"]["arguments"]["session_id"], "123e4567-e89b-12d3-a456-426614174000")
        self.assertIn("curl_example", payload)
        self.assertIn("migration_hint", payload)

    def test_legacy_premium_fleet_summary_missing_controller_id_has_tool_specific_example(self):
        client = TestClient(server_mod.app)

        with patch.object(server_mod, "call_tool", new=AsyncMock()) as call_mock:
            response = client.get(
                "/api/v1/premium/fleet-summary",
                headers={"accept": "application/json"},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 422)
        call_mock.assert_not_awaited()
        payload = response.json()
        self.assertEqual(payload["tool_name"], "generate_fleet_summary")
        self.assertEqual(payload["missing"], ["controller_id"])
        self.assertEqual(payload["mcp_example"]["params"]["arguments"]["controller_id"], "stable-controller-id")

    def test_legacy_x402_get_without_args_returns_utility_schema_hint(self):
        client = TestClient(server_mod.app)

        response = client.get("/api/v1/x402/email-validate", follow_redirects=False)

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "DELX-UTIL-1001")
        self.assertEqual(payload["surface"], "delx-agent-utilities")
        self.assertEqual(payload["status"], "missing_required_input")
        self.assertEqual(payload["required"], ["email"])
        self.assertEqual(payload["legacy_endpoint"], "https://api.delx.ai/api/v1/x402/email-validate")

    def test_legacy_x402_post_executes_utility_in_public_mode(self):
        client = TestClient(server_mod.app)

        response = client.post("/api/v1/x402/email-validate", json={"email": "hello@example.com"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["surface"], "delx-agent-utilities")
        self.assertTrue(payload["compatibility_route"])
        self.assertEqual(payload["tool_name"], "util_email_validate")
        self.assertTrue(payload["result"]["syntax_valid"])

    def test_canonical_utilities_route_executes_utility(self):
        client = TestClient(server_mod.app)

        response = client.get("/api/v1/utilities/dns-lookup?domain=delx.ai")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["compatibility_route"])
        self.assertEqual(payload["tool_name"], "util_dns_lookup")
        self.assertGreaterEqual(payload["result"]["answer_count"], 1)

    def test_product_catalog_exposes_six_monetizable_utilities(self):
        client = TestClient(server_mod.app)

        response = client.get("/api/v1/utilities/catalog")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        product_ids = {product["product_id"] for product in payload["products"]}
        products_by_id = {product["product_id"]: product for product in payload["products"]}
        self.assertEqual(payload["surface"], "delx-agent-utilities")
        self.assertEqual(payload["count"], 6)
        self.assertEqual(
            product_ids,
            {
                "website_intelligence_report",
                "domain_trust_report",
                "mcp_server_readiness_report",
                "api_integration_readiness",
                "x402_server_audit",
                "company_contact_pack",
            },
        )
        for product in payload["products"]:
            self.assertFalse(product["monetization"]["paid_candidate"])
            self.assertTrue(product["monetization"]["future_paid_candidate"])
            self.assertTrue(product["price"]["free_access"])
            self.assertTrue(product["canonical_endpoint"].startswith("https://api.delx.ai/api/v1/utilities/"))
            self.assertTrue(product["x402_endpoint"].startswith("https://api.delx.ai/api/v1/x402/"))
        self.assertEqual(products_by_id["domain_trust_report"]["price"]["amount"], "0.00")
        self.assertEqual(products_by_id["domain_trust_report"]["price"]["future_amount"], "0.01")
        self.assertEqual(products_by_id["mcp_server_readiness_report"]["price"]["amount"], "0.00")
        self.assertEqual(products_by_id["mcp_server_readiness_report"]["price"]["future_amount"], "0.05")
        self.assertEqual(
            products_by_id["mcp_server_readiness_report"]["x402_endpoint"],
            "https://api.delx.ai/api/v1/x402/mcp-server-readiness",
        )

    def test_utilities_list_marks_productized_tools(self):
        client = TestClient(server_mod.app)

        response = client.get("/api/v1/utilities")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["product_count"], 6)
        tools_by_name = {tool["name"]: tool for tool in payload["tools"]}
        self.assertEqual(
            tools_by_name["util_domain_trust_report"]["product"]["product_id"],
            "domain_trust_report",
        )
        self.assertEqual(
            tools_by_name["util_website_intelligence_report"]["product"]["price"]["amount"],
            "0.00",
        )
        self.assertEqual(
            tools_by_name["util_website_intelligence_report"]["product"]["price"]["future_amount"],
            "0.01",
        )
        self.assertEqual(
            tools_by_name["util_mcp_server_readiness_report"]["product"]["price"]["amount"],
            "0.00",
        )
        self.assertEqual(
            tools_by_name["util_mcp_server_readiness_report"]["product"]["price"]["future_amount"],
            "0.05",
        )
        self.assertEqual(tools_by_name["util_uuid_generate"]["slug"], "uuid")
        self.assertEqual(tools_by_name["util_cron_describe"]["slug"], "cron")
        self.assertEqual(payload["api_key"]["header"], "x-delx-api-key")
        self.assertEqual(payload["api_key"]["create_url"], "https://api.delx.ai/api/v1/utilities/api-keys")

    def test_utility_api_key_creation_and_invalid_key_validation(self):
        client = TestClient(server_mod.app)

        response = client.post(
            "/api/v1/utilities/api-keys",
            json={"agent_id": "qa-agent", "label": "QA Agent"},
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertTrue(payload["api_key"].startswith("dux_"))
        self.assertEqual(payload["key_prefix"], payload["api_key"][:12])
        self.assertNotIn("key_hash", payload)

        invalid = client.get(
            "/api/v1/utilities/domain-trust-report?domain=delx.ai",
            headers={"x-delx-api-key": "dux_invalid"},
        )
        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(invalid.json()["error"], "invalid_utility_api_key")

    def test_productized_missing_input_returns_product_metadata(self):
        client = TestClient(server_mod.app)

        response = client.get("/api/v1/utilities/domain-trust-report", follow_redirects=False)

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "missing_required_input")
        self.assertEqual(payload["missing"], ["url"])
        self.assertEqual(payload["product"]["product_id"], "domain_trust_report")
        self.assertEqual(payload["product"]["price"]["amount"], "0.00")
        self.assertEqual(payload["product"]["price"]["future_amount"], "0.01")

    def test_slug_aliases_resolve_to_canonical_utilities(self):
        client = TestClient(server_mod.app)

        uuid_response = client.get("/api/v1/utilities/uuid-generate")
        cron_response = client.get("/api/v1/utilities/cron-describe?expression=*/5%20*%20*%20*%20*")
        audit_response = client.get("/api/v1/utilities/x402-server-audit?url=https://example.com")

        self.assertEqual(uuid_response.status_code, 200)
        self.assertEqual(uuid_response.json()["tool_name"], "util_uuid_generate")
        self.assertEqual(cron_response.status_code, 200)
        self.assertEqual(cron_response.json()["tool_name"], "util_cron_describe")
        self.assertEqual(audit_response.status_code, 200)
        self.assertEqual(audit_response.json()["tool_name"], "util_x402_server_audit")

    def test_domain_alias_is_accepted_for_domain_trust_report(self):
        client = TestClient(server_mod.app)

        with patch.object(
            server_mod,
            "call_util_tool",
            new=AsyncMock(return_value={"url": "https://example.com", "domain": "example.com", "trust_score": 70}),
        ) as call_mock:
            response = client.get("/api/v1/utilities/domain-trust-report?domain=example.com")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(call_mock.call_args.args[1]["url"], "example.com")

    def test_domain_trust_headers_match_paid_product_catalog(self):
        client = TestClient(server_mod.app)
        with patch.object(
            server_mod,
            "call_util_tool",
            new=AsyncMock(return_value={"url": "https://example.com", "domain": "example.com", "trust_score": 70}),
        ):
            response = client.get("/api/v1/utilities/domain-trust-report?url=https://example.com")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("x-delx-utility-charge-mode", response.headers)
        payload = response.json()
        self.assertFalse(payload["monetization"]["charge_enabled"])
        self.assertFalse(payload["monetization"]["shadow_only"])

    def test_productized_success_includes_agent_report_and_metering(self):
        client = TestClient(server_mod.app)

        async def fake_call_util_tool(*args, **kwargs):
            return {
                "page": {"reachable": True, "title": "Delx"},
                "summary": {"title": "Delx"},
                "links": {"link_count": 7},
                "forms": {"form_count": 1},
                "contacts": {"emails": ["support@delx.ai"]},
            }

        key_response = client.post(
            "/api/v1/utilities/api-keys",
            json={"agent_id": "metered-agent", "label": "Metered Agent"},
        )
        api_key = key_response.json()["api_key"]

        with patch.object(server_mod, "call_util_tool", side_effect=fake_call_util_tool):
            response = client.post(
                "/api/v1/utilities/website-intelligence-report",
                json={"url": "https://delx.ai", "source": "pytest"},
                headers={"x-delx-api-key": api_key},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["agent_report"]["product_id"], "website_intelligence_report")
        self.assertEqual(payload["api_key"]["key_prefix"], api_key[:12])

        original_pin = server_mod.settings.PROTOCOL_ADMIN_PIN
        try:
            server_mod.settings.PROTOCOL_ADMIN_PIN = "test"
            dashboard = client.get("/api/v1/admin/utility-metering?pin=test&days=1")
            ops_panel = client.get("/api/v1/admin/utility-ops?pin=test")
        finally:
            server_mod.settings.PROTOCOL_ADMIN_PIN = original_pin
        self.assertEqual(dashboard.status_code, 200)
        metering = dashboard.json()
        self.assertGreaterEqual(metering["totals"]["calls"], 1)
        self.assertGreaterEqual(metering["totals"]["active_api_keys"], 1)

        self.assertEqual(ops_panel.status_code, 200)
        self.assertIn("cards", ops_panel.json())
        self.assertIn("12h", ops_panel.json()["windows"])

    def test_tool_schema_includes_product_metadata_for_productized_utility(self):
        client = TestClient(server_mod.app)

        response = client.get("/api/v1/tools/schema/util_api_integration_readiness")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["surface"], "delx-agent-utilities")
        self.assertEqual(payload["product"]["product_id"], "api_integration_readiness")
        self.assertEqual(payload["product"]["canonical_endpoint"], "https://api.delx.ai/api/v1/utilities/api-integration-readiness")

    def test_shadow_charge_mode_marks_selected_utility_without_blocking(self):
        client = TestClient(server_mod.app)
        original_mode = server_mod.settings.MONETIZATION_UTILITY_CHARGE_MODE
        original_tools = server_mod.settings.MONETIZATION_UTILITY_CHARGE_TOOLS
        try:
            server_mod.settings.MONETIZATION_UTILITY_CHARGE_MODE = "shadow"
            server_mod.settings.MONETIZATION_UTILITY_CHARGE_TOOLS = "util_website_intelligence_report"
            response = client.get("/api/v1/x402/website-intelligence-report", follow_redirects=False)
        finally:
            server_mod.settings.MONETIZATION_UTILITY_CHARGE_MODE = original_mode
            server_mod.settings.MONETIZATION_UTILITY_CHARGE_TOOLS = original_tools

        self.assertEqual(response.status_code, 422)
        self.assertNotIn("x-delx-utility-charge-mode", response.headers)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["monetization"]["price_usdc"], "0.00")

    def test_enforce_charge_mode_requires_payment_for_productized_x402_route(self):
        client = TestClient(server_mod.app)
        with paid_utility_rollout(
            "enforce",
            (
                "util_website_intelligence_report,util_domain_trust_report,"
                "util_mcp_server_readiness_report,util_api_integration_readiness,"
                "util_x402_server_audit,util_company_contact_pack"
            ),
        ):
            with patch.object(server_mod, "call_util_tool", new=AsyncMock()) as call_mock:
                response = client.post(
                    "/api/v1/x402/domain-trust-report",
                    json={"url": "https://delx.ai", "timeout": 8},
                    headers={"x-delx-source": "pytest"},
                )

        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.headers["x-402-version"], "2")
        self.assertIn("payment-required", response.headers)
        self.assertFalse(response.headers["payment-required"].lstrip().startswith("{"))
        payload = response.json()
        self.assertEqual(payload["x402Version"], 2)
        self.assertEqual(payload["accepts"][0]["resource"], "https://api.delx.ai/api/v1/x402/domain-trust-report")
        self.assertEqual(payload["accepts"][0]["amount"], "10000")
        self.assertEqual(payload["accepts"][0]["maxAmountRequired"], "10000")
        call_mock.assert_not_called()

    def test_enforce_charge_mode_requires_payment_for_mcp_readiness_x402_route(self):
        client = TestClient(server_mod.app)
        with paid_utility_rollout("enforce", "util_mcp_server_readiness_report"):
            with patch.object(server_mod, "call_util_tool", new=AsyncMock()) as call_mock:
                response = client.post(
                    "/api/v1/x402/mcp-server-readiness",
                    json={"url": "https://api.delx.ai", "timeout": 8},
                    headers={"x-delx-source": "pytest"},
                )

        self.assertEqual(response.status_code, 402)
        payload = response.json()
        self.assertEqual(payload["accepts"][0]["resource"], "https://api.delx.ai/api/v1/x402/mcp-server-readiness")
        self.assertEqual(payload["accepts"][0]["amount"], "50000")
        self.assertEqual(payload["accepts"][0]["maxAmountRequired"], "50000")
        call_mock.assert_not_called()

    def test_enforce_charge_mode_requires_payment_for_canonical_utility_route(self):
        client = TestClient(server_mod.app)
        with paid_utility_rollout(
            "enforce",
            (
                "util_website_intelligence_report,util_domain_trust_report,"
                "util_api_integration_readiness,util_x402_server_audit,util_company_contact_pack"
            ),
        ):
            with patch.object(server_mod, "call_util_tool", new=AsyncMock()) as call_mock:
                response = client.get(
                    "/api/v1/utilities/website-intelligence-report?url=https://delx.ai",
                    headers={"x-delx-source": "pytest"},
                )

        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.headers["x-402-version"], "2")
        self.assertEqual(response.headers["x-delx-product"], "agent-tools")
        self.assertEqual(response.headers["x-delx-surface"], "utilities")
        self.assertEqual(response.headers["x-delx-utility-charge-mode"], "enforce")
        self.assertEqual(response.headers["x-delx-utility-price-usdc"], "0.01")
        payload = response.json()
        self.assertEqual(payload["x402Version"], 2)
        self.assertEqual(payload["accepts"][0]["resource"], "https://api.delx.ai/api/v1/utilities/website-intelligence-report")
        self.assertEqual(payload["accepts"][0]["amount"], "10000")
        self.assertEqual(payload["accepts"][0]["maxAmountRequired"], "10000")
        call_mock.assert_not_called()

    def test_enforce_charge_mode_validates_canonical_utility_before_payment(self):
        client = TestClient(server_mod.app)
        with paid_utility_rollout("enforce", "util_website_intelligence_report"):
            with patch.object(server_mod, "call_util_tool", new=AsyncMock()) as call_mock:
                response = client.get(
                    "/api/v1/utilities/website-intelligence-report",
                    headers={"x-delx-source": "pytest"},
                )

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "DELX-UTIL-1001")
        self.assertEqual(payload["missing"], ["url"])
        self.assertEqual(payload["schema_url"], "https://api.delx.ai/api/v1/tools/schema/util_website_intelligence_report")
        self.assertNotIn("payment-required", response.headers)
        call_mock.assert_not_called()

    def test_enforce_charge_mode_uses_metered_price_for_mcp_utility_402(self):
        client = TestClient(server_mod.app)
        with paid_utility_rollout(
            "enforce",
            (
                "util_website_intelligence_report,util_domain_trust_report,"
                "util_api_integration_readiness,util_x402_server_audit,util_company_contact_pack"
            ),
        ):
            with (
                patch.object(server_mod, "call_util_tool", new=AsyncMock()) as call_mock,
                patch("x402_guard.X402Middleware._coinbase_bazaar_state", new=AsyncMock()) as bazaar_mock,
            ):
                response = client.post(
                    "/v1/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "util_domain_trust_report",
                            "arguments": {"url": "https://delx.ai", "timeout": 8},
                        },
                    },
                    headers={"x-delx-source": "pytest"},
                )

        self.assertEqual(response.status_code, 402)
        payload = response.json()
        self.assertEqual(payload["x402Version"], 2)
        self.assertEqual(payload["accepts"][0]["resource"], "https://delx.ai/mcp/tools/util_domain_trust_report")
        self.assertEqual(payload["accepts"][0]["amount"], "10000")
        self.assertEqual(payload["accepts"][0]["maxAmountRequired"], "10000")
        self.assertIn("$0.01 USDC", payload["error"])
        call_mock.assert_not_called()
        bazaar_mock.assert_not_called()

    def test_free_mode_premium_post_reaches_current_free_tool_handler(self):
        client = TestClient(server_mod.app)

        async def fake_call_tool(*args, **kwargs):
            return [TextContent(type="text", text=json.dumps({"ok": True, "session_id": "sess-123"}))]

        with (
            patch.object(server_mod, "call_tool", side_effect=fake_call_tool),
        ):
            response = client.post("/api/v1/premium/session-summary", json={"session_id": "sess-123"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["tool_name"], "get_session_summary")
        self.assertIn("content", payload)

    def test_free_mode_premium_get_without_args_redirects_to_tool_schema(self):
        client = TestClient(server_mod.app)

        response = client.get("/api/v1/premium/controller-brief", follow_redirects=False)

        self.assertEqual(response.status_code, 307)
        self.assertEqual(
            response.headers["location"],
            "https://api.delx.ai/api/v1/tools/schema/generate_controller_brief",
        )


if __name__ == "__main__":
    unittest.main()
