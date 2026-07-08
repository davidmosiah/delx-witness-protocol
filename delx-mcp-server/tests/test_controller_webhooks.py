import asyncio
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MethodType
from unittest import mock

from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server as server_mod
from controller_webhooks import create_controller_webhook_record, delivery_allowed, fold_controller_webhooks
from supabase_store import SupabaseSessionStore


class ControllerWebhookTests(unittest.TestCase):
    def test_create_controller_webhook_record_normalizes_payload(self):
        item = create_controller_webhook_record(
            "openclaw-main",
            "https://example.com/webhook",
            events=["incident", "score_drop", "invalid"],
            threshold=22,
            cooldown_min=15,
        )
        self.assertEqual(item["controller_id"], "openclaw-main")
        self.assertEqual(item["events"], ["incident", "score_drop"])
        self.assertEqual(item["threshold"], 22)
        self.assertEqual(item["cooldown_min"], 15)
        self.assertTrue(item["webhook_id"].startswith("wh_"))

    def test_fold_controller_webhooks_keeps_active_latest_state(self):
        rows = [
            {
                "event_type": "controller_webhook_registered",
                "timestamp": "2026-03-05T10:00:00+00:00",
                "metadata": {
                    "webhook_id": "wh_1",
                    "controller_id": "openclaw-main",
                    "callback_url": "https://example.com/a",
                    "events": ["score_drop"],
                    "threshold": 30,
                    "cooldown_min": 20,
                },
            },
            {
                "event_type": "controller_webhook_sent",
                "timestamp": "2026-03-05T10:05:00+00:00",
                "metadata": {"webhook_id": "wh_1", "event": "score_drop"},
            },
            {
                "event_type": "controller_webhook_registered",
                "timestamp": "2026-03-05T10:01:00+00:00",
                "metadata": {
                    "webhook_id": "wh_2",
                    "controller_id": "openclaw-main",
                    "callback_url": "https://example.com/b",
                    "events": ["incident"],
                    "threshold": 40,
                    "cooldown_min": 30,
                },
            },
            {
                "event_type": "controller_webhook_deactivated",
                "timestamp": "2026-03-05T10:06:00+00:00",
                "metadata": {"webhook_id": "wh_2"},
            },
        ]
        items = fold_controller_webhooks(rows)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "wh_1")
        self.assertEqual(items[0]["last_delivery_event"], "score_drop")
        self.assertTrue(items[0]["last_delivery_success"])

    def test_delivery_allowed_respects_cooldown(self):
        webhook = {"id": "wh_1", "cooldown_min": 30}
        now = datetime(2026, 3, 5, 11, 0, tzinfo=timezone.utc)
        recent = [
            {
                "event_type": "controller_webhook_sent",
                "timestamp": (now - timedelta(minutes=10)).isoformat(),
                "metadata": {"webhook_id": "wh_1", "event": "incident"},
            }
        ]
        self.assertFalse(delivery_allowed(webhook, "incident", recent, now))
        self.assertTrue(delivery_allowed(webhook, "score_drop", recent, now))


class SupabaseControllerWebhookRegisterTests(unittest.IsolatedAsyncioTestCase):
    async def test_register_controller_webhook_returns_item_without_post_write_name_error(self):
        store = SupabaseSessionStore()

        async def fake_ensure(self, controller_id: str):
            return {"id": "session-123"}

        async def fake_log_event(self, agent_id: str, event_type: str, session_id: str | None = None, metadata: dict | None = None):
            return None

        store.ensure_controller_session = MethodType(fake_ensure, store)
        store.log_event = MethodType(fake_log_event, store)

        item = await store.register_controller_webhook(
            "audit-controller",
            "https://example.com/webhook",
            events=["score_drop", "incident"],
            threshold=35,
            cooldown_min=30,
        )

        self.assertEqual(item["controller_id"], "audit-controller")
        self.assertEqual(item["events"], ["incident", "score_drop"])
        self.assertEqual(item["threshold"], 35)
        self.assertEqual(item["cooldown_min"], 30)
        self.assertTrue(item["id"].startswith("wh_"))
        self.assertTrue(item["created_at"].endswith("+00:00"))


class FleetWebhookTestRouteContracts(unittest.IsolatedAsyncioTestCase):
    async def test_fleet_webhook_test_falls_back_when_overview_lookup_times_out(self):
        deliveries: list[dict[str, object]] = []
        callback_calls: list[dict[str, object]] = []

        class _FakeStore:
            async def list_controller_webhooks(self, controller_id: str):
                return [
                    {
                        "id": "wh_test",
                        "callback_url": "https://example.com/webhook",
                    }
                ]

            async def get_fleet_overview(self, controller_id: str, days: int = 7):
                raise asyncio.TimeoutError("fleet overview too slow")

            async def log_controller_webhook_delivery(
                self,
                controller_id: str,
                webhook_id: str,
                *,
                event: str,
                callback_url: str,
                success: bool,
                status_code: int | None = None,
                payload: dict | None = None,
                is_test: bool = False,
            ):
                deliveries.append(
                    {
                        "controller_id": controller_id,
                        "webhook_id": webhook_id,
                        "event": event,
                        "callback_url": callback_url,
                        "success": success,
                        "status_code": status_code,
                        "payload": payload,
                        "is_test": is_test,
                    }
                )

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                return None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url: str, json: dict | None = None):
                callback_calls.append({"url": url, "json": json})

                class _Response:
                    status_code = 200

                return _Response()

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/fleet/audit-controller/webhooks/test",
            "headers": [],
            "path_params": {"controller_id": "audit-controller"},
            "query_string": b"",
        }

        with mock.patch.object(server_mod, "store", _FakeStore()), mock.patch.object(server_mod.httpx, "AsyncClient", _FakeAsyncClient):
            response = await server_mod.fleet_webhooks_test(Request(scope))

        payload = json.loads(response.body.decode("utf-8"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["results"][0]["success"], True)
        self.assertEqual(callback_calls[0]["url"], "https://example.com/webhook")
        self.assertEqual(
            callback_calls[0]["json"]["data"],
            {"agents_total": None, "active_alerts": None, "active_patterns": None},
        )
        self.assertEqual(deliveries[0]["is_test"], True)


if __name__ == "__main__":
    unittest.main()
