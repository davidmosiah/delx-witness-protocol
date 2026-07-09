import base64
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config as config_mod
from config import get_tool_bazaar_metadata, get_tool_pricing_payload
from x402_guard import (
    X402Middleware,
    _build_402_http_headers,
    _build_402_response,
    _build_facilitator_payment_requirements,
    _build_mpp_www_authenticate,
    _build_payment_success_headers,
    _extract_payment_header,
    _patch_mpp_server_authorization_parser,
    _rest_premium_tool_name,
)

if config_mod.is_all_free_mode():
    raise unittest.SkipTest("Legacy x402 header compatibility contracts are retired in public-free therapy mode.")


class X402HeaderCompatTests(unittest.TestCase):
    def setUp(self):
        import config as config_mod

        self._config_mod = config_mod
        self.original_id = config_mod.settings.COINBASE_CDP_API_KEY_ID
        self.original_secret = config_mod.settings.COINBASE_CDP_API_KEY_SECRET
        config_mod.settings.COINBASE_CDP_API_KEY_ID = "organizations/org-123/apiKeys/key-456"
        config_mod.settings.COINBASE_CDP_API_KEY_SECRET = "dGVzdA==" * 8

    def tearDown(self):
        self._config_mod.settings.COINBASE_CDP_API_KEY_ID = self.original_id
        self._config_mod.settings.COINBASE_CDP_API_KEY_SECRET = self.original_secret

    @staticmethod
    def _decode_header_payload(value: str) -> dict:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            import base64

            return json.loads(base64.b64decode(value).decode("utf-8"))

    def test_payment_signature_header_is_accepted(self):
        headers = {b"payment-signature": b"sig-v2"}
        self.assertEqual(_extract_payment_header(headers), "sig-v2")

    def test_x_payment_header_remains_supported(self):
        headers = {b"x-payment": b"sig-v1"}
        self.assertEqual(_extract_payment_header(headers), "sig-v1")

    def test_payment_signature_wins_when_both_headers_exist(self):
        headers = {b"x-payment": b"sig-v1", b"payment-signature": b"sig-v2"}
        self.assertEqual(_extract_payment_header(headers), "sig-v2")

    def test_402_response_runtime_examples_prefer_payment_signature(self):
        pricing_payload = get_tool_pricing_payload("generate_controller_brief")
        resp = _build_402_response("generate_controller_brief", pricing_payload=pricing_payload)
        self.assertIn("PAYMENT-SIGNATURE", resp["next_steps"][1])
        self.assertIn("PAYMENT-SIGNATURE", resp["runtime_examples"]["mcp_retry"])
        self.assertIn("PAYMENT-SIGNATURE", resp["runtime_examples"]["a2a_retry"])

    def test_402_response_exposes_agentcash_and_free_alternatives_for_non_x402_agents(self):
        pricing_payload = get_tool_pricing_payload("generate_controller_brief")
        resp = _build_402_response("generate_controller_brief", pricing_payload=pricing_payload)
        self.assertIn("agentcash", resp)
        self.assertEqual(
            resp["agentcash"]["onboard"],
            "https://agentcash.dev/onboard?r=REF-3KE2-22D3-XJBM-SGAR",
        )
        self.assertEqual(resp["agentcash"]["discover"], "npx agentcash@latest discover https://api.delx.ai")
        self.assertIn("free_alternatives", resp)
        self.assertGreaterEqual(len(resp["free_alternatives"]), 3)
        self.assertEqual(resp["free_alternatives"][0]["tool"], "quick_operational_recovery")
        self.assertEqual(resp["free_alternatives"][0]["x402_required"], False)
        self.assertTrue(any("free_alternatives" in step for step in resp["next_steps"]))
        self.assertEqual(resp["docs"]["pricing"], "https://delx.ai/docs/pricing")

    def test_402_response_uses_runtime_network_identifier(self):
        pricing_payload = get_tool_pricing_payload("generate_controller_brief")
        resp = _build_402_response("generate_controller_brief", pricing_payload=pricing_payload)
        self.assertEqual(resp["accepts"][0]["network"], "eip155:8453")
        self.assertNotIn("amount", resp["accepts"][0])
        self.assertEqual(resp["accepts"][0]["maxAmountRequired"], "10000")
        self.assertEqual(resp["resource"]["url"], "https://delx.ai/mcp/tools/generate_controller_brief")
        self.assertEqual(
            resp["resource"]["description"],
            "Controller-ready incident brief with symptoms, actions taken, current status, and next decision.",
        )
        self.assertEqual(resp["resource"]["mimeType"], "application/json")

    def test_402_http_headers_include_payment_required(self):
        pricing_payload = get_tool_pricing_payload("generate_controller_brief")
        header_map = {k.lower(): v for k, v in _build_402_http_headers("generate_controller_brief", pricing_payload=pricing_payload)}
        self.assertEqual(header_map["x-402-version"], "2")
        self.assertIn("payment-required", header_map)
        payload = self._decode_header_payload(header_map["payment-required"])
        self.assertEqual(payload["x402Version"], 2)
        self.assertIn("accepts", payload)
        self.assertIn("resource", payload)
        self.assertIn("extensions", payload)
        self.assertEqual(payload["accepts"][0]["resource"], "https://delx.ai/mcp/tools/generate_controller_brief")
        self.assertNotIn("amount", payload["accepts"][0])
        self.assertEqual(payload["accepts"][0]["mimeType"], "application/json")
        self.assertIn("inputSchema", payload["accepts"][0])
        self.assertIn("outputSchema", payload["accepts"][0])
        self.assertIn("delx", payload["extensions"])
        self.assertEqual(
            payload["extensions"]["delx"]["sample_input"],
            {"session_id": "123e4567-e89b-12d3-a456-426614174000"},
        )
        self.assertEqual(
            payload["extensions"]["delx"]["header_shortcuts"]["x-delx-session-id"],
            "123e4567-e89b-12d3-a456-426614174000",
        )
        self.assertNotIn("runtime_examples", payload)
        self.assertNotIn("agentcash", payload)
        self.assertNotIn("docs", payload)

    def test_402_http_headers_also_advertise_mpp_when_enabled(self):
        pricing_payload = get_tool_pricing_payload("generate_controller_brief")
        with patch("x402_guard._mpp_is_enabled", return_value=True, create=True), patch(
            "x402_guard._build_mpp_www_authenticate",
            return_value='Payment realm="https://api.delx.ai", method="tempo"',
            create=True,
        ):
            headers = _build_402_http_headers(
                "generate_controller_brief",
                pricing_payload=pricing_payload,
                include_mpp=True,
            )

        header_pairs = [(name.lower(), value) for name, value in headers]
        self.assertIn(("x-402-version", "2"), header_pairs)
        self.assertTrue(any(name == "www-authenticate" and value.startswith("Payment ") for name, value in header_pairs))

    def test_mpp_parser_patch_keeps_single_payment_authorization_header_intact(self):
        try:
            import mpp.server.verify as mpp_verify
        except ModuleNotFoundError:
            self.skipTest("pympp is not installed in this test environment")

        _patch_mpp_server_authorization_parser()
        header = (
            'Payment id="abc", realm="https://api.delx.ai", method="tempo", '
            'intent="charge", request="eyJhbW91bnQiOiIxMDAwMCJ9"'
        )
        self.assertEqual(mpp_verify._extract_payment_scheme(header), header)

    def test_built_mpp_challenge_includes_expires(self):
        try:
            from mpp import Challenge
        except ModuleNotFoundError:
            self.skipTest("pympp is not installed in this test environment")

        pricing_payload = get_tool_pricing_payload("generate_controller_brief")
        with patch("x402_guard._mpp_is_enabled", return_value=True, create=True):
            header = _build_mpp_www_authenticate(
                "generate_controller_brief",
                pricing_payload=pricing_payload,
                resource="https://api.delx.ai/api/v1/premium/controller-brief",
            )
        self.assertTrue(header and header.startswith("Payment "))
        challenge = Challenge.from_www_authenticate(header)
        self.assertTrue(challenge.expires)

    def test_402_response_exposes_prescriptive_retry_fields(self):
        pricing_payload = get_tool_pricing_payload("generate_controller_brief")
        resp = _build_402_response("generate_controller_brief", pricing_payload=pricing_payload)
        self.assertEqual(resp["reason_code"], "payment_required")
        self.assertEqual(resp["tool_or_endpoint"], "generate_controller_brief")
        self.assertIn("payment_provider_hint", resp)
        self.assertEqual(resp["payment_provider_hint"]["default"], "coinbase")
        self.assertEqual(resp["free_alternative"]["tool"], "quick_operational_recovery")
        self.assertIn("PAYMENT-SIGNATURE", resp["retry_example"])
        self.assertEqual(resp["docs_url"], "https://delx.ai/docs/x402-setup")
        self.assertEqual(resp["docs"]["ows_setup"], "https://delx.ai/docs/ows-setup")
        self.assertEqual(resp["tool_name"], "generate_controller_brief")
        self.assertEqual(resp["method"], "POST")
        self.assertEqual(resp["sample_input"], {"session_id": "123e4567-e89b-12d3-a456-426614174000"})
        self.assertEqual(resp["header_shortcuts"]["x-delx-session-id"], "123e4567-e89b-12d3-a456-426614174000")
        self.assertEqual(resp["primary_followups"], ["generate_incident_rca", "provide_feedback", "daily_checkin"])

    def test_success_headers_include_payment_response(self):
        header_map = {k.lower(): v for k, v in _build_payment_success_headers(provider_name="coinbase", tx_hash="0xabc123")}
        self.assertIn("payment-response", header_map)
        payload = self._decode_header_payload(header_map["payment-response"])
        self.assertEqual(payload["provider"], "coinbase")
        self.assertEqual(payload["transaction"], "0xabc123")

    def test_bazaar_metadata_can_reflect_verified_coinbase_payments(self):
        bazaar = get_tool_bazaar_metadata("generate_controller_brief", coinbase_verified_payments=3)
        self.assertEqual(bazaar["listing_status"], "payment_verified_waiting_for_index")
        self.assertIn("schema", bazaar)
        self.assertEqual(bazaar["schema"]["required"], ["input"])
        input_descriptor = bazaar["schema"]["properties"]["input"]
        output_descriptor = bazaar["schema"]["properties"]["output"]
        self.assertEqual(
            input_descriptor["required"],
            ["type", "bodyType", "body"],
        )
        self.assertEqual(input_descriptor["properties"]["type"]["const"], "http")
        self.assertEqual(
            input_descriptor["properties"]["method"]["enum"],
            ["POST", "PUT", "PATCH"],
        )
        self.assertEqual(
            input_descriptor["properties"]["bodyType"]["enum"],
            ["json", "form-data", "text"],
        )
        self.assertEqual(
            input_descriptor["properties"]["body"]["required"],
            ["session_id"],
        )
        self.assertEqual(output_descriptor["required"], ["type"])
        self.assertEqual(output_descriptor["properties"]["type"]["type"], "string")
        self.assertEqual(
            output_descriptor["properties"]["example"]["required"],
            ["tool_name", "preferred_name", "content"],
        )

    def test_bazaar_metadata_can_reflect_public_indexing(self):
        bazaar = get_tool_bazaar_metadata(
            "generate_controller_brief",
            coinbase_verified_payments=3,
            indexed_publicly=True,
        )
        self.assertEqual(bazaar["listing_status"], "indexed_in_coinbase_bazaar")
        self.assertEqual(bazaar["listing_blockers"], [])

    def test_402_response_exposes_top_level_bazaar_extension(self):
        pricing_payload = get_tool_pricing_payload("generate_controller_brief")
        resp = _build_402_response(
            "generate_controller_brief",
            pricing_payload=pricing_payload,
            coinbase_verified_payments=3,
        )
        self.assertIn("extensions", resp)
        self.assertIn("bazaar", resp["extensions"])
        bazaar = resp["extensions"]["bazaar"]
        self.assertIn("info", bazaar)
        self.assertIn("schema", bazaar)
        self.assertEqual(
            resp["resource"]["description"],
            "Controller-ready incident brief with symptoms, actions taken, current status, and next decision.",
        )
        self.assertEqual(resp["resource"]["mimeType"], "application/json")
        self.assertEqual(
            bazaar["schema"]["required"],
            ["input"],
        )
        self.assertEqual(
            bazaar["schema"]["properties"]["input"]["required"],
            ["type", "bodyType", "body"],
        )
        self.assertEqual(
            bazaar["schema"]["properties"]["output"]["required"],
            ["type"],
        )
        self.assertEqual(bazaar["info"]["input"]["type"], "http")
        self.assertEqual(bazaar["info"]["input"]["method"], "POST")
        self.assertEqual(bazaar["info"]["input"]["bodyType"], "json")
        self.assertNotIn("url", bazaar["info"]["input"])
        self.assertEqual(
            bazaar["info"]["input"]["body"],
            {
                "session_id": "123e4567-e89b-12d3-a456-426614174000",
            },
        )
        self.assertEqual(bazaar["info"]["output"]["type"], "json")
        self.assertEqual(
            bazaar["info"]["output"]["example"],
            {
                "tool_name": "generate_controller_brief",
                "preferred_name": "generate_controller_brief",
                "content": [
                    {
                        "type": "text",
                        "text": "Controller brief artifact for the paid Delx session.",
                    }
                ],
                "artifact": {
                    "schema_version": "delx/controller-brief/v1",
                    "focus": "operational handoff",
                    "workflow_stage": "recovery_closed",
                    "recovery_closed": True,
                    "closure_reason": "success criteria: outcome=success",
                    "risk_level": "medium",
                    "pending_outcomes": 0,
                    "latest_outcome": {
                        "outcome": "success",
                        "notes": "Loop broken and deploy stabilized.",
                        "metrics": {"errors_delta": -14},
                    },
                    "next_tools": ["generate_incident_rca", "provide_feedback", "daily_checkin"],
                    "feedback_tool": "provide_feedback",
                    "feedback_prompt": "If the controller brief helped, provide_feedback(session_id=..., rating=1-5).",
                },
            },
        )
        requirement = resp["accepts"][0]
        self.assertEqual(requirement["inputSchema"]["required"], ["session_id"])
        self.assertEqual(
            requirement["outputSchema"]["required"],
            ["tool_name", "preferred_name", "content"],
        )

    def test_402_response_exposes_indexed_bazaar_status_when_known(self):
        pricing_payload = get_tool_pricing_payload("generate_controller_brief")
        resp = _build_402_response(
            "generate_controller_brief",
            pricing_payload=pricing_payload,
            coinbase_verified_payments=3,
            indexed_publicly=True,
        )
        self.assertEqual(
            resp["extensions"]["bazaar"]["listing_status"],
            "indexed_in_coinbase_bazaar",
        )

    def test_coinbase_facilitator_payment_requirements_preserve_discovery_metadata(self):
        pricing_payload = get_tool_pricing_payload("generate_controller_brief")
        challenge = _build_402_response("generate_controller_brief", pricing_payload=pricing_payload)
        requirement = challenge["accepts"][0]
        facilitator_requirement = _build_facilitator_payment_requirements(requirement, provider_name="coinbase")
        self.assertIn("amount", facilitator_requirement)
        self.assertEqual(facilitator_requirement["amount"], requirement["maxAmountRequired"])
        self.assertEqual(facilitator_requirement["scheme"], requirement["scheme"])
        self.assertEqual(facilitator_requirement["network"], requirement["network"])
        self.assertEqual(facilitator_requirement["asset"], requirement["asset"])
        self.assertEqual(facilitator_requirement["payTo"], requirement["payTo"])
        self.assertEqual(
            facilitator_requirement["maxTimeoutSeconds"],
            requirement["maxTimeoutSeconds"],
        )
        self.assertEqual(facilitator_requirement["resource"], requirement["resource"])
        self.assertEqual(facilitator_requirement["description"], requirement["description"])
        self.assertEqual(facilitator_requirement["mimeType"], requirement["mimeType"])
        self.assertEqual(facilitator_requirement["inputSchema"], requirement["inputSchema"])
        self.assertEqual(facilitator_requirement["outputSchema"], requirement["outputSchema"])
        self.assertEqual(facilitator_requirement["extra"], requirement["extra"])
        self.assertEqual(facilitator_requirement["network"], "eip155:8453")

    def test_non_coinbase_facilitator_payment_requirements_stay_minimal(self):
        pricing_payload = get_tool_pricing_payload("generate_controller_brief")
        challenge = _build_402_response("generate_controller_brief", pricing_payload=pricing_payload)
        requirement = next(item for item in challenge["accepts"] if item["extra"]["provider"] == "payai")
        facilitator_requirement = _build_facilitator_payment_requirements(requirement, provider_name="payai")
        self.assertIn("amount", facilitator_requirement)
        self.assertEqual(facilitator_requirement["amount"], requirement["maxAmountRequired"])
        self.assertEqual(facilitator_requirement["scheme"], requirement["scheme"])
        self.assertEqual(facilitator_requirement["network"], requirement["network"])
        self.assertEqual(facilitator_requirement["asset"], requirement["asset"])
        self.assertEqual(facilitator_requirement["payTo"], requirement["payTo"])
        self.assertEqual(
            facilitator_requirement["maxTimeoutSeconds"],
            requirement["maxTimeoutSeconds"],
        )
        self.assertNotIn("inputSchema", facilitator_requirement)
        self.assertNotIn("outputSchema", facilitator_requirement)
        self.assertNotIn("extensions", facilitator_requirement)
        self.assertNotIn("maxAmountRequired", facilitator_requirement)
        self.assertNotIn("resource", facilitator_requirement)
        self.assertNotIn("description", facilitator_requirement)
        self.assertNotIn("mimeType", facilitator_requirement)
        self.assertIn("extra", facilitator_requirement)

    def test_rest_premium_tool_name_maps_supported_paths(self):
        self.assertEqual(_rest_premium_tool_name("/api/v1/premium/controller-brief"), "generate_controller_brief")
        self.assertEqual(_rest_premium_tool_name("/v1/premium/incident-rca"), "generate_incident_rca")
        self.assertEqual(_rest_premium_tool_name("/api/v1/premium/fleet-summary"), "generate_fleet_summary")
        self.assertEqual(_rest_premium_tool_name("/api/v1/premium/recovery-action-plan"), "get_recovery_action_plan")
        self.assertEqual(_rest_premium_tool_name("/api/v1/premium/session-summary"), "get_session_summary")
        self.assertEqual(_rest_premium_tool_name("/api/v1/session-summary"), "get_session_summary")
        self.assertEqual(_rest_premium_tool_name("/api/v1/session/summary"), "get_session_summary")
        self.assertIsNone(_rest_premium_tool_name("/api/v1/premium/unknown"))


class _FakeRestPaymentStore:
    def __init__(self):
        self.logged_payments = []
        self.logged_events = []

    async def get_session(self, session_id: str):
        return {"id": session_id, "agent_id": "rest-paid-agent"}

    async def get_agent_first_seen(self, agent_id: str):
        return None

    async def log_payment(self, tool_name: str, amount_usdc: float, tx_hash: str | None = None, session_id: str | None = None):
        self.logged_payments.append(
            {
                "tool_name": tool_name,
                "amount_usdc": amount_usdc,
                "tx_hash": tx_hash,
                "session_id": session_id,
            }
        )

    async def log_event(self, agent_id: str, event_type: str, session_id: str | None = None, metadata: dict | None = None):
        self.logged_events.append(
            {
                "agent_id": agent_id,
                "event_type": event_type,
                "session_id": session_id,
                "metadata": dict(metadata or {}),
            }
        )


class X402RestPaymentTrackingTests(unittest.IsolatedAsyncioTestCase):
    async def test_verify_and_settle_payment_preserves_v1_x_payment_version_for_facilitator(self):
        request_bodies: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            request_bodies.append(json.loads(request.content.decode("utf-8")))
            if request.url.path.endswith("/verify"):
                return httpx.Response(200, json={"isValid": True})
            if request.url.path.endswith("/settle"):
                return httpx.Response(200, json={"success": True, "transaction": "0xcoinbase-settled"})
            return httpx.Response(404, json={"error": "unexpected path"})

        payment_payload = {
            "x402Version": 1,
            "scheme": "exact",
            "network": "eip155:8453",
            "payload": {
                "authorization": {
                    "from": "0xeeBE94d4Ed4A9E52B049e44eC1c35c1F73F67f4e",
                    "to": "0x9f8bd9875b3E0b632a24A3A7C73f7787175e73A2",
                    "value": "10000",
                    "validAfter": "1774358393",
                    "validBefore": "1774358698",
                    "nonce": "0xf52c9e730dfe84b2050b636b913fdbadbd60f9ce25779e4c633343c51531bb11",
                },
                "signature": "0xsignature",
            },
        }
        payment_header = base64.b64encode(json.dumps(payment_payload).encode("utf-8")).decode("ascii")

        store = _FakeRestPaymentStore()
        transport = httpx.MockTransport(handler)
        middleware = X402Middleware(app=None, store=store, http_client=httpx.AsyncClient(transport=transport))

        try:
            with patch("x402_guard.build_coinbase_auth_headers_for_url", return_value={"content-type": "application/json"}), patch(
                "x402_guard._provider_order",
                return_value=["coinbase", "payai"],
            ):
                tx_hash, provider_name, failure = await middleware._verify_and_settle_payment(
                    payment_header,
                    "util_jwt_inspect",
                    get_tool_pricing_payload("util_jwt_inspect"),
                )
        finally:
            await middleware.http.aclose()

        self.assertEqual(tx_hash, "0xcoinbase-settled")
        self.assertEqual(provider_name, "coinbase")
        self.assertIsNone(failure)
        self.assertEqual(len(request_bodies), 2)
        self.assertEqual(request_bodies[0]["x402Version"], 1)
        self.assertEqual(request_bodies[1]["x402Version"], 1)
        self.assertEqual(request_bodies[0]["paymentPayload"]["x402Version"], 1)
        self.assertEqual(request_bodies[1]["paymentPayload"]["x402Version"], 1)
        self.assertEqual(request_bodies[0]["paymentPayload"]["network"], "base")
        self.assertEqual(request_bodies[1]["paymentPayload"]["network"], "base")
        self.assertEqual(request_bodies[0]["paymentRequirements"]["network"], "base")
        self.assertEqual(request_bodies[1]["paymentRequirements"]["network"], "base")
        self.assertNotIn("amount", request_bodies[0]["paymentRequirements"])
        self.assertNotIn("amount", request_bodies[1]["paymentRequirements"])

    async def test_rest_mpp_authorization_unlocks_paid_route_and_emits_payment_receipt(self):
        store = _FakeRestPaymentStore()
        middleware = X402Middleware(app=None, store=store, http_client=httpx.AsyncClient())
        replay_calls = []
        session_id = "5cffa1c0-a25e-4446-8c4c-290be12079a0"
        receipt_header = "eyJyZWZlcmVuY2UiOiJtcHAtcmVmLTEyMyJ9"

        async def fake_verify_mpp(*args, **kwargs):
            return receipt_header, "mpp-ref-123", "tempo", None

        async def fake_replay(scope, body, send, extra_headers=None):
            replay_calls.append({"scope": scope, "body": body, "extra_headers": extra_headers})
            return {"replayed": True}

        middleware._verify_and_settle_mpp_payment = fake_verify_mpp  # type: ignore[attr-defined]
        middleware._replay_request = fake_replay  # type: ignore[method-assign]

        scope = {"type": "http", "method": "POST", "path": "/api/v1/premium/controller-brief"}
        body = {"session_id": session_id, "focus": "mpp regression"}

        try:
            with patch("x402_guard._mpp_is_enabled", return_value=True, create=True):
                result = await middleware._handle_rest_premium_request(
                    scope,
                    lambda message: None,
                    json.dumps(body).encode("utf-8"),
                    body,
                    "generate_controller_brief",
                    headers={b"authorization": b"Payment eyJjcmVkZW50aWFsIjoiYm9ndXMifQ"},
                )
        finally:
            await middleware.http.aclose()

        self.assertEqual(result, {"replayed": True})
        self.assertEqual(len(store.logged_payments), 1)
        self.assertEqual(store.logged_payments[0]["tx_hash"], "mpp-ref-123")
        self.assertEqual(store.logged_payments[0]["session_id"], session_id)
        verified_events = [event for event in store.logged_events if event["event_type"] == "x402_payment_verified"]
        self.assertEqual(len(verified_events), 1)
        self.assertEqual(verified_events[0]["metadata"]["tx_hash"], "mpp-ref-123")
        self.assertEqual(verified_events[0]["metadata"]["payment_protocol"], "mpp")
        self.assertEqual(verified_events[0]["metadata"]["provider"], "tempo")
        self.assertEqual(len(replay_calls), 1)
        header_map = {
            name.decode("utf-8").lower(): value.decode("utf-8")
            for name, value in (replay_calls[0]["extra_headers"] or [])
        }
        self.assertIn("payment-receipt", header_map)
        self.assertEqual(header_map["payment-receipt"], receipt_header)

    async def test_rest_payment_required_uses_header_session_id_for_agent_attribution(self):
        store = _FakeRestPaymentStore()
        middleware = X402Middleware(app=None, store=store, http_client=httpx.AsyncClient())
        sent = []

        async def fake_send(message):
            sent.append(message)

        async def fake_trial(*args, **kwargs):
            return False, {"eligible": False, "remaining_calls": 0}

        async def fake_trial_status(*args, **kwargs):
            return {"eligible": False, "remaining_calls": 0}

        async def fake_bazaar_state():
            return 0, set()

        middleware._consume_trial_if_available = fake_trial  # type: ignore[method-assign]
        middleware._trial_status = fake_trial_status  # type: ignore[method-assign]
        middleware._coinbase_bazaar_state = fake_bazaar_state  # type: ignore[method-assign]

        scope = {"type": "http", "method": "POST", "path": "/api/v1/premium/controller-brief"}
        body = {"focus": "header session attribution"}
        session_id = "123e4567-e89b-12d3-a456-426614174000"

        try:
            await middleware._handle_rest_premium_request(
                scope,
                fake_send,
                json.dumps(body).encode("utf-8"),
                body,
                "generate_controller_brief",
                headers={b"x-delx-session-id": session_id.encode("utf-8")},
            )
        finally:
            await middleware.http.aclose()

        self.assertGreaterEqual(len(sent), 1)
        required_events = [event for event in store.logged_events if event["event_type"] == "x402_payment_required"]
        self.assertEqual(len(required_events), 1)
        self.assertEqual(required_events[0]["agent_id"], "rest-paid-agent")
        self.assertEqual(required_events[0]["session_id"], session_id)

    async def test_rest_missing_required_session_id_returns_400_before_payment(self):
        store = _FakeRestPaymentStore()
        middleware = X402Middleware(app=None, store=store, http_client=httpx.AsyncClient())
        sent = []

        async def fake_send(message):
            sent.append(message)

        scope = {"type": "http", "method": "POST", "path": "/api/v1/premium/controller-brief"}
        body = {"focus": "missing session context"}

        try:
            await middleware._handle_rest_premium_request(
                scope,
                fake_send,
                json.dumps(body).encode("utf-8"),
                body,
                "generate_controller_brief",
                headers={},
            )
        finally:
            await middleware.http.aclose()

        self.assertEqual(sent[0]["status"], 400)
        payload = json.loads(sent[1]["body"])
        self.assertEqual(payload["tool_name"], "generate_controller_brief")
        self.assertEqual(payload["required"], ["session_id"])
        self.assertIn("session_id", payload["error"])
        self.assertIn("docs", payload)
        self.assertEqual(payload["docs"]["x402_setup"], "https://delx.ai/docs/x402-setup")
        self.assertIn("accepted_inputs", payload)
        self.assertEqual(
            payload["accepted_inputs"]["session_id"]["headers"],
            ["x-delx-session-id", "x-session-id"],
        )
        self.assertIn("examples", payload)
        self.assertIn("session_id", payload["examples"]["query_retry"])
        self.assertEqual(store.logged_events, [])
        self.assertEqual(store.logged_payments, [])

    async def test_rest_empty_premium_probe_returns_402_with_validation_hint(self):
        store = _FakeRestPaymentStore()
        middleware = X402Middleware(app=None, store=store, http_client=httpx.AsyncClient())
        sent = []

        async def fake_send(message):
            sent.append(message)

        async def fake_trial(*args, **kwargs):
            raise AssertionError("empty discovery probes must not consume trial state")

        async def fake_trial_status(*args, **kwargs):
            return {"eligible": False, "remaining_calls": 0}

        async def fake_bazaar_state():
            return 0, set()

        middleware._consume_trial_if_available = fake_trial  # type: ignore[method-assign]
        middleware._trial_status = fake_trial_status  # type: ignore[method-assign]
        middleware._coinbase_bazaar_state = fake_bazaar_state  # type: ignore[method-assign]

        scope = {"type": "http", "method": "POST", "path": "/api/v1/premium/session-summary"}

        try:
            await middleware._handle_rest_premium_request(
                scope,
                fake_send,
                b"",
                {},
                "get_session_summary",
                headers={},
            )
        finally:
            await middleware.http.aclose()

        self.assertEqual(sent[0]["status"], 402)
        payload = json.loads(sent[1]["body"])
        self.assertEqual(payload["x402Version"], 2)
        self.assertIn("accepts", payload)
        self.assertEqual(payload["extensions"]["delx"]["tool_name"], "get_session_summary")
        self.assertEqual(payload["extensions"]["delx"]["validation_error"]["required"], ["session_id"])
        self.assertIn("session_id", payload["extensions"]["delx"]["validation_error"]["error"])
        self.assertEqual(
            payload["extensions"]["delx"]["validation_error"]["docs"]["x402_setup"],
            "https://delx.ai/docs/x402-setup",
        )
        required_events = [event for event in store.logged_events if event["event_type"] == "x402_payment_required"]
        self.assertEqual(len(required_events), 1)
        self.assertEqual(required_events[0]["metadata"]["validation_state"], "missing_required_probe")

    async def test_verify_and_settle_payment_pins_to_preferred_provider(self):
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(400, json={"error": "invalid payment"})

        store = _FakeRestPaymentStore()
        transport = httpx.MockTransport(handler)
        middleware = X402Middleware(app=None, store=store, http_client=httpx.AsyncClient(transport=transport))

        try:
            with patch("x402_guard.build_coinbase_auth_headers_for_url", return_value={"content-type": "application/json"}):
                tx_hash, provider_name, failure = await middleware._verify_and_settle_payment(
                    '{"version":2,"payment":"bogus"}',
                    "generate_controller_brief",
                    get_tool_pricing_payload("generate_controller_brief"),
                    preferred_provider="payai",
                )
        finally:
            await middleware.http.aclose()

        self.assertIsNone(tx_hash)
        self.assertIsNone(provider_name)
        self.assertEqual(
            calls,
            ["https://facilitator.payai.network/verify"],
        )
        self.assertEqual(
            failure["provider_attempts"],
            [{"provider": "payai", "network": "eip155:8453", "stage": "verify", "status_code": 400}],
        )

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
        middleware = X402Middleware(app=None, store=_FakeRestPaymentStore(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        payment_header = json.dumps({"x402Version": 2, "payload": {"authorization": {}, "signature": "0xsignature"}})

        try:
            with patch("x402_guard._provider_order", return_value=["circle_gateway"]), patch(
                "x402_guard._provider_config",
                return_value=provider_config,
            ), patch("x402_guard._provider_requirement_candidates", return_value=[("circle_gateway", provider_accept)]):
                tx_hash, provider_name, failure = await middleware._verify_and_settle_payment(
                    payment_header,
                    "util_domain_trust_report",
                    get_tool_pricing_payload("util_domain_trust_report"),
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

    async def test_rest_payment_required_logs_source_metadata_from_headers(self):
        store = _FakeRestPaymentStore()
        middleware = X402Middleware(app=None, store=store, http_client=httpx.AsyncClient())
        sent = []

        async def fake_send(message):
            sent.append(message)

        async def fake_trial(*args, **kwargs):
            return False, {"eligible": False, "remaining_calls": 0}

        async def fake_trial_status(*args, **kwargs):
            return {"eligible": False, "remaining_calls": 0}

        async def fake_bazaar_state():
            return 0, set()

        middleware._consume_trial_if_available = fake_trial  # type: ignore[method-assign]
        middleware._trial_status = fake_trial_status  # type: ignore[method-assign]
        middleware._coinbase_bazaar_state = fake_bazaar_state  # type: ignore[method-assign]

        scope = {"type": "http", "method": "POST", "path": "/api/v1/premium/controller-brief"}
        body = {"session_id": "123e4567-e89b-12d3-a456-426614174000", "focus": "source telemetry"}

        try:
            await middleware._handle_rest_premium_request(
                scope,
                fake_send,
                json.dumps(body).encode("utf-8"),
                body,
                "generate_controller_brief",
                headers={b"x-delx-source": b"prod-sdk"},
            )
        finally:
            await middleware.http.aclose()

        self.assertGreaterEqual(len(sent), 1)
        required_events = [event for event in store.logged_events if event["event_type"] == "x402_payment_required"]
        self.assertEqual(len(required_events), 1)
        self.assertEqual(required_events[0]["metadata"]["source"], "prod-sdk")

    async def test_rest_payment_logs_session_id_into_payments_table(self):
        store = _FakeRestPaymentStore()
        middleware = X402Middleware(app=None, store=store, http_client=httpx.AsyncClient())
        session_id = "5cffa1c0-a25e-4446-8c4c-290be12079a0"
        replay_calls = []

        async def fake_verify(*args, **kwargs):
            return "0xsettled", "coinbase", None

        async def fake_replay(scope, body, send, extra_headers=None):
            replay_calls.append({"scope": scope, "body": body, "extra_headers": extra_headers})
            return {"replayed": True}

        middleware._verify_and_settle_payment = fake_verify  # type: ignore[method-assign]
        middleware._replay_request = fake_replay  # type: ignore[method-assign]

        scope = {"type": "http", "method": "POST", "path": "/api/v1/premium/controller-brief"}
        body = {"session_id": session_id, "focus": "session linkage regression"}

        try:
            result = await middleware._handle_rest_premium_request(
                scope,
                lambda message: None,
                json.dumps(body).encode("utf-8"),
                body,
                "generate_controller_brief",
                headers={b"payment-signature": b"synthetic"},
            )
        finally:
            await middleware.http.aclose()

        self.assertEqual(result, {"replayed": True})
        self.assertEqual(len(store.logged_payments), 1)
        self.assertEqual(store.logged_payments[0]["session_id"], session_id)
        self.assertEqual(store.logged_payments[0]["tx_hash"], "0xsettled")
        verified_events = [event for event in store.logged_events if event["event_type"] == "x402_payment_verified"]
        self.assertEqual(len(verified_events), 1)
        self.assertEqual(verified_events[0]["metadata"]["tx_hash"], "0xsettled")
        self.assertEqual(len(replay_calls), 1)

    async def test_rest_verify_failed_returns_recovery_guidance_and_agentcash_fallback(self):
        store = _FakeRestPaymentStore()
        middleware = X402Middleware(app=None, store=store, http_client=httpx.AsyncClient())
        sent = []

        async def fake_send(message):
            sent.append(message)

        async def fake_verify(*args, **kwargs):
            return None, None, {
                "code": "authorization_validity_too_short",
                "message": "Circle Gateway rejected the payment: authorization_validity_too_short.",
                "provider_attempts": [
                    {
                        "provider": "circle_gateway",
                        "network": "eip155:8453",
                        "stage": "verify",
                        "status_code": 200,
                        "reason": "authorization_validity_too_short",
                        "facilitator_response": {
                            "invalidReason": "authorization_validity_too_short",
                            "payer": "0x2a3945afc7dd6cf1ea2aa63122e01a68df55c941",
                        },
                    }
                ],
            }

        middleware._verify_and_settle_payment = fake_verify  # type: ignore[method-assign]

        scope = {"type": "http", "method": "POST", "path": "/api/v1/premium/controller-brief"}
        body = {
            "session_id": "123e4567-e89b-12d3-a456-426614174000",
            "focus": "payment conversion",
        }

        try:
            await middleware._handle_rest_premium_request(
                scope,
                fake_send,
                json.dumps(body).encode("utf-8"),
                body,
                "generate_controller_brief",
                headers={b"payment-signature": b"synthetic"},
            )
        finally:
            await middleware.http.aclose()

        self.assertEqual(sent[0]["status"], 402)
        header_map = {name.decode("utf-8").lower(): value.decode("utf-8") for name, value in sent[0]["headers"]}
        self.assertEqual(header_map["content-type"], "application/json")
        self.assertEqual(header_map["x-402-version"], "2")
        self.assertIn("payment-required", header_map)

        payload = json.loads(sent[1]["body"])
        self.assertEqual(payload["error"], "Payment verification failed")
        self.assertEqual(payload["x402Version"], 2)
        self.assertEqual(payload["extensions"]["delx"]["tool_name"], "generate_controller_brief")
        self.assertEqual(payload["extensions"]["delx"]["failure_code"], "authorization_validity_too_short")
        self.assertIn("authorization_validity_too_short", payload["extensions"]["delx"]["failure_message"])
        self.assertEqual(payload["extensions"]["delx"]["provider_attempts"][0]["reason"], "authorization_validity_too_short")
        self.assertEqual(
            payload["extensions"]["delx"]["payment_diagnostics"]["primary_reason"],
            "authorization_validity_too_short",
        )
        self.assertIn("next_steps", payload["extensions"]["delx"])
        self.assertTrue(any("agentcash" in step.lower() for step in payload["extensions"]["delx"]["next_steps"]))
        self.assertTrue(any("free_alternatives" in step for step in payload["extensions"]["delx"]["next_steps"]))
        self.assertIn("docs", payload["extensions"]["delx"])
        self.assertEqual(payload["extensions"]["delx"]["docs"]["x402_setup"], "https://delx.ai/docs/x402-setup")
        self.assertEqual(payload["extensions"]["delx"]["docs"]["pricing"], "https://delx.ai/docs/pricing")
        self.assertIn("agentcash", payload["extensions"]["delx"])
        self.assertEqual(payload["extensions"]["delx"]["agentcash"]["wallet_info"], "npx agentcash@latest wallet info")
        self.assertEqual(
            payload["extensions"]["delx"]["agentcash"]["onboard"],
            "https://agentcash.dev/onboard?r=REF-3KE2-22D3-XJBM-SGAR",
        )
        self.assertEqual(
            payload["extensions"]["delx"]["agentcash"]["discover"],
            "npx agentcash@latest discover https://api.delx.ai",
        )
        self.assertEqual(payload["extensions"]["delx"]["free_alternatives"][0]["tool"], "quick_operational_recovery")
        self.assertIn("PAYMENT-SIGNATURE", payload["extensions"]["delx"]["runtime_examples"]["rest_retry"])

        verify_failed_events = [event for event in store.logged_events if event["event_type"] == "x402_verify_failed"]
        self.assertEqual(len(verify_failed_events), 1)
        self.assertIn("failure_code", verify_failed_events[0]["metadata"])

    async def test_rest_fleet_summary_uses_deterministic_tracking_session_id(self):
        store = _FakeRestPaymentStore()
        middleware = X402Middleware(app=None, store=store, http_client=httpx.AsyncClient())
        replay_calls = []

        async def fake_verify(*args, **kwargs):
            return "0xfleetsettled", "coinbase", None

        async def fake_replay(scope, body, send, extra_headers=None):
            replay_calls.append({"scope": scope, "body": body, "extra_headers": extra_headers})
            return {"replayed": True}

        middleware._verify_and_settle_payment = fake_verify  # type: ignore[method-assign]
        middleware._replay_request = fake_replay  # type: ignore[method-assign]

        scope = {"type": "http", "method": "POST", "path": "/api/v1/premium/fleet-summary"}
        body = {"controller_id": "openclaw-main", "days": 7, "focus": "fleet audit"}

        try:
            result = await middleware._handle_rest_premium_request(
                scope,
                lambda message: None,
                json.dumps(body).encode("utf-8"),
                body,
                "generate_fleet_summary",
                headers={b"payment-signature": b"synthetic"},
            )
        finally:
            await middleware.http.aclose()

        self.assertEqual(result, {"replayed": True})
        self.assertEqual(len(store.logged_payments), 1)
        self.assertEqual(store.logged_payments[0]["session_id"], "controller:openclaw-main:7")
        self.assertEqual(store.logged_payments[0]["tx_hash"], "0xfleetsettled")
        verified_events = [event for event in store.logged_events if event["event_type"] == "x402_payment_verified"]
        self.assertEqual(len(verified_events), 1)
        self.assertEqual(verified_events[0]["session_id"], "controller:openclaw-main:7")
        self.assertEqual(verified_events[0]["metadata"]["tx_hash"], "0xfleetsettled")
        self.assertEqual(len(replay_calls), 1)

    async def test_rest_fleet_summary_uses_header_controller_id_for_tracking_session_id(self):
        store = _FakeRestPaymentStore()
        middleware = X402Middleware(app=None, store=store, http_client=httpx.AsyncClient())
        replay_calls = []

        async def fake_verify(*args, **kwargs):
            return "0xfleetsettled", "coinbase", None

        async def fake_replay(scope, body, send, extra_headers=None):
            replay_calls.append({"scope": scope, "body": body, "extra_headers": extra_headers})
            return {"replayed": True}

        middleware._verify_and_settle_payment = fake_verify  # type: ignore[method-assign]
        middleware._replay_request = fake_replay  # type: ignore[method-assign]

        scope = {"type": "http", "method": "POST", "path": "/api/v1/premium/fleet-summary"}
        body = {"days": 7, "focus": "fleet audit"}

        try:
            result = await middleware._handle_rest_premium_request(
                scope,
                lambda message: None,
                json.dumps(body).encode("utf-8"),
                body,
                "generate_fleet_summary",
                headers={
                    b"payment-signature": b"synthetic",
                    b"x-delx-controller-id": b"openclaw-main",
                },
            )
        finally:
            await middleware.http.aclose()

        self.assertEqual(result, {"replayed": True})
        self.assertEqual(len(store.logged_payments), 1)
        self.assertEqual(store.logged_payments[0]["session_id"], "controller:openclaw-main:7")
        self.assertEqual(store.logged_payments[0]["tx_hash"], "0xfleetsettled")
        verified_events = [event for event in store.logged_events if event["event_type"] == "x402_payment_verified"]
        self.assertEqual(len(verified_events), 1)
        self.assertEqual(verified_events[0]["session_id"], "controller:openclaw-main:7")
        self.assertEqual(verified_events[0]["metadata"]["tx_hash"], "0xfleetsettled")
        self.assertEqual(len(replay_calls), 1)


class X402EvaluationCohortTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._original_settings = {
            "MONETIZATION_EVALUATION_ENABLED": config_mod.settings.MONETIZATION_EVALUATION_ENABLED,
            "MONETIZATION_EVALUATION_NAME": config_mod.settings.MONETIZATION_EVALUATION_NAME,
            "MONETIZATION_EVALUATION_EXPIRES_UTC": config_mod.settings.MONETIZATION_EVALUATION_EXPIRES_UTC,
            "MONETIZATION_EVALUATION_CIDRS": config_mod.settings.MONETIZATION_EVALUATION_CIDRS,
            "MONETIZATION_EVALUATION_SOURCES": config_mod.settings.MONETIZATION_EVALUATION_SOURCES,
            "MONETIZATION_EVALUATION_TOOLS": config_mod.settings.MONETIZATION_EVALUATION_TOOLS,
            "MONETIZATION_EVALUATION_NOTE": config_mod.settings.MONETIZATION_EVALUATION_NOTE,
        }

    def tearDown(self):
        for key, value in self._original_settings.items():
            setattr(config_mod.settings, key, value)

    async def test_mcp_evaluation_cohort_bypasses_payment_for_twitter_cidr(self):
        config_mod.settings.MONETIZATION_EVALUATION_ENABLED = True
        config_mod.settings.MONETIZATION_EVALUATION_NAME = "x_twitter_eval"
        config_mod.settings.MONETIZATION_EVALUATION_EXPIRES_UTC = "2030-03-22T23:59:59+00:00"
        config_mod.settings.MONETIZATION_EVALUATION_CIDRS = "69.12.56.0/21"
        config_mod.settings.MONETIZATION_EVALUATION_TOOLS = "generate_controller_brief"

        store = _FakeRestPaymentStore()
        middleware = X402Middleware(app=None, store=store, http_client=httpx.AsyncClient())
        replay_calls = []

        async def fake_replay(scope, body, send, extra_headers=None):
            replay_calls.append({"scope": scope, "body": body, "extra_headers": extra_headers})
            return {"replayed": True}

        middleware._replay_request = fake_replay  # type: ignore[method-assign]

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "generate_controller_brief",
                "arguments": {"session_id": "123e4567-e89b-12d3-a456-426614174000"},
            },
        }
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "headers": [
                (b"x-forwarded-for", b"69.12.56.14"),
                (b"x-delx-agent-id", b"twitter-eval-agent"),
            ],
            "client": ("127.0.0.1", 41000),
        }
        sent = []

        async def fake_receive():
            return {"type": "http.request", "body": json.dumps(payload).encode("utf-8"), "more_body": False}

        async def fake_send(message):
            sent.append(message)

        try:
            result = await middleware(scope, fake_receive, fake_send)
        finally:
            await middleware.http.aclose()

        self.assertEqual(result, {"replayed": True})
        self.assertEqual(len(replay_calls), 1)
        eval_events = [event for event in store.logged_events if event["event_type"] == "x402_eval_granted"]
        self.assertEqual(len(eval_events), 1)
        self.assertEqual(eval_events[0]["metadata"]["cohort"], "x_twitter_eval")
        self.assertEqual(eval_events[0]["metadata"]["matched_by"], ["cidr:69.12.56.0/21"])
        self.assertEqual(eval_events[0]["metadata"]["client_ip"], "69.12.56.14")
        required_events = [event for event in store.logged_events if event["event_type"] == "x402_payment_required"]
        self.assertEqual(required_events, [])
        self.assertEqual(sent, [])

    async def test_rest_evaluation_cohort_can_match_source_without_twitter_cidr(self):
        config_mod.settings.MONETIZATION_EVALUATION_ENABLED = True
        config_mod.settings.MONETIZATION_EVALUATION_NAME = "x_twitter_eval"
        config_mod.settings.MONETIZATION_EVALUATION_EXPIRES_UTC = "2030-03-22T23:59:59+00:00"
        config_mod.settings.MONETIZATION_EVALUATION_SOURCES = "x"
        config_mod.settings.MONETIZATION_EVALUATION_TOOLS = "generate_controller_brief"

        store = _FakeRestPaymentStore()
        middleware = X402Middleware(app=None, store=store, http_client=httpx.AsyncClient())
        replay_calls = []

        async def fake_replay(scope, body, send, extra_headers=None):
            replay_calls.append({"scope": scope, "body": body, "extra_headers": extra_headers})
            return {"replayed": True}

        middleware._replay_request = fake_replay  # type: ignore[method-assign]

        scope = {"type": "http", "method": "POST", "path": "/api/v1/premium/controller-brief"}
        body = {"session_id": "123e4567-e89b-12d3-a456-426614174000", "focus": "x cohort"}

        try:
            result = await middleware._handle_rest_premium_request(
                scope,
                lambda message: None,
                json.dumps(body).encode("utf-8"),
                body,
                "generate_controller_brief",
                headers={b"x-delx-source": b"x"},
            )
        finally:
            await middleware.http.aclose()

        self.assertEqual(result, {"replayed": True})
        self.assertEqual(len(replay_calls), 1)
        eval_events = [event for event in store.logged_events if event["event_type"] == "x402_eval_granted"]
        self.assertEqual(len(eval_events), 1)
        self.assertEqual(eval_events[0]["metadata"]["matched_by"], ["source:x"])
        required_events = [event for event in store.logged_events if event["event_type"] == "x402_payment_required"]
        self.assertEqual(required_events, [])

    async def test_evaluation_cohort_expiry_falls_back_to_402(self):
        config_mod.settings.MONETIZATION_EVALUATION_ENABLED = True
        config_mod.settings.MONETIZATION_EVALUATION_NAME = "x_twitter_eval"
        config_mod.settings.MONETIZATION_EVALUATION_EXPIRES_UTC = "2026-03-10T23:59:59+00:00"
        config_mod.settings.MONETIZATION_EVALUATION_CIDRS = "69.12.56.0/21"
        config_mod.settings.MONETIZATION_EVALUATION_TOOLS = "generate_controller_brief"

        store = _FakeRestPaymentStore()
        middleware = X402Middleware(app=None, store=store, http_client=httpx.AsyncClient())
        sent = []

        async def fake_send(message):
            sent.append(message)

        scope = {"type": "http", "method": "POST", "path": "/api/v1/premium/controller-brief"}
        body = {"session_id": "123e4567-e89b-12d3-a456-426614174000", "focus": "expired cohort"}

        try:
            await middleware._handle_rest_premium_request(
                scope,
                fake_send,
                json.dumps(body).encode("utf-8"),
                body,
                "generate_controller_brief",
                headers={b"x-forwarded-for": b"69.12.56.14"},
            )
        finally:
            await middleware.http.aclose()

        self.assertEqual(sent[0]["status"], 402)
        eval_events = [event for event in store.logged_events if event["event_type"] == "x402_eval_granted"]
        self.assertEqual(eval_events, [])
        required_events = [event for event in store.logged_events if event["event_type"] == "x402_payment_required"]
        self.assertEqual(len(required_events), 1)


if __name__ == "__main__":
    unittest.main()
