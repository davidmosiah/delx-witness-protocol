import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config as config_mod
from x402_guard import X402Middleware


class _NoopStore:
    async def log_event(self, *args, **kwargs):
        return None


class X402A2APaymentContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_paid_a2a_request_without_payment_returns_402_instead_of_name_error(self):
        middleware = X402Middleware(app=None, store=_NoopStore(), http_client=httpx.AsyncClient())
        sent: list[dict] = []

        async def fake_send(message: dict):
            sent.append(message)

        async def no_grant(*args, **kwargs):
            return False, {"eligible": False}

        async def trial_status(*args, **kwargs):
            return {"eligible": False, "remaining_calls": 0}

        pricing = dict(config_mod.get_tool_pricing_payload("a2a_message_send"))
        pricing["price_cents"] = 1

        middleware._consume_evaluation_if_available = no_grant  # type: ignore[method-assign]
        middleware._consume_trial_if_available = no_grant  # type: ignore[method-assign]
        middleware._trial_status = trial_status  # type: ignore[method-assign]

        try:
            with (
                patch("x402_guard.get_tool_pricing_payload", return_value=pricing),
                patch("x402_guard.should_enforce_utility_charge", return_value=True),
            ):
                try:
                    await middleware._handle_a2a_request(
                        {"type": "http", "method": "POST", "path": "/v1/a2a"},
                        fake_send,
                        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": {}}).encode(),
                        {"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": {}},
                        headers={},
                    )
                except NameError as exc:
                    self.fail(f"A2A payment challenge referenced an undefined name: {exc}")
        finally:
            await middleware.http.aclose()

        self.assertGreaterEqual(len(sent), 2)
        self.assertEqual(sent[0]["status"], 402)


if __name__ == "__main__":
    unittest.main()
