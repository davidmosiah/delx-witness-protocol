import sys
import unittest
from pathlib import Path

from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server as server_mod
from product_surfaces import classify_request_surface, classify_tool_surface, product_metadata_for_tool


class ProductSurfaceContractTests(unittest.TestCase):
    def test_request_classifier_separates_protocol_discovery_health_and_tools(self):
        self.assertEqual(classify_request_surface(path="/v1/mcp").product, "protocol")
        self.assertEqual(classify_request_surface(path="/v1/mcp").metrics_bucket, "protocol_mcp")
        self.assertEqual(classify_request_surface(path="/v1/a2a").metrics_bucket, "protocol_a2a")
        self.assertEqual(classify_request_surface(path="/api/v1/mcp/start").product, "discovery")
        self.assertEqual(classify_request_surface(path="/api/v1/status").metrics_bucket, "health_probe")

        x402 = classify_request_surface(path="/api/v1/x402/dns-lookup")
        self.assertEqual(x402.product, "agent-tools")
        self.assertEqual(x402.metrics_bucket, "tools_legacy_x402")
        self.assertTrue(x402.compatibility_route)

        premium = classify_request_surface(path="/api/v1/premium/controller-brief")
        self.assertEqual(premium.product, "protocol")
        self.assertEqual(premium.metrics_bucket, "protocol_secondary_export")
        self.assertTrue(premium.compatibility_route)

    def test_tool_classifier_uses_utility_prefix_and_export_list(self):
        self.assertEqual(classify_tool_surface("util_dns_lookup").product, "agent-tools")
        self.assertEqual(classify_tool_surface("util_dns_lookup").metrics_bucket, "tools_real_call")
        self.assertEqual(classify_tool_surface("generate_controller_brief").metrics_bucket, "protocol_secondary_export")
        self.assertEqual(classify_tool_surface("reflect").metrics_bucket, "protocol_session")

    def test_tool_metadata_is_event_ready(self):
        meta = product_metadata_for_tool("util_tls_inspect")
        self.assertEqual(meta["product"], "agent-tools")
        self.assertEqual(meta["product_surface"], "agent_tools")
        self.assertEqual(meta["metrics_bucket"], "tools_real_call")
        self.assertEqual(meta["canonical_url"], "https://delx.ai/utilities")

    def test_runtime_headers_mark_protocol_and_legacy_utility_surfaces(self):
        client = TestClient(server_mod.app)

        protocol = client.get("/api/v1/mcp/start")
        self.assertEqual(protocol.status_code, 200)
        self.assertEqual(protocol.headers["x-delx-product"], "discovery")
        self.assertEqual(protocol.headers["x-delx-metrics-bucket"], "discovery_probe")
        self.assertEqual(protocol.headers["x-delx-canonical-url"], "https://delx.ai/docs/discovery")

        legacy = client.get("/api/v1/x402/dns-lookup", headers={"accept": "application/json"})
        self.assertEqual(legacy.status_code, 422)
        self.assertEqual(legacy.headers["x-delx-product"], "agent-tools")
        self.assertEqual(legacy.headers["x-delx-metrics-bucket"], "tools_legacy_x402")
        self.assertEqual(legacy.headers["x-delx-canonical-url"], "https://delx.ai/utilities")
        self.assertEqual(legacy.headers["x-delx-compatibility-route"], "true")


if __name__ == "__main__":
    unittest.main()
