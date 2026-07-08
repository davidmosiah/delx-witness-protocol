import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from payment_session_backfill import build_payment_agent_attribution, build_payment_session_backfill_plan


class PaymentSessionBackfillPlanTests(unittest.TestCase):
    def test_build_payment_agent_attribution_prefers_direct_session_agent_mapping(self):
        payments = [
            {
                "id": 21,
                "tool_name": "generate_controller_brief",
                "session_id": "sid-1",
                "tx_hash": "0xaaa",
                "timestamp": "2026-03-11T10:00:00.000000+00:00",
            }
        ]

        attribution = build_payment_agent_attribution(
            payments,
            [],
            session_agent_map={"sid-1": "agent-direct"},
        )

        self.assertEqual(len(attribution), 1)
        self.assertEqual(attribution[0]["action"], "attributed")
        self.assertEqual(attribution[0]["reason"], "payment_session_id")
        self.assertEqual(attribution[0]["attributed_agent_id"], "agent-direct")

    def test_build_payment_agent_attribution_recovers_agent_from_verified_event_without_session_ref(self):
        payments = [
            {
                "id": 22,
                "tool_name": "generate_controller_brief",
                "session_id": None,
                "tx_hash": "0xbbb",
                "timestamp": "2026-03-11T10:00:00.000000+00:00",
            }
        ]
        events = [
            {
                "id": 201,
                "event_type": "x402_payment_verified",
                "session_id": None,
                "agent_id": "agent-inferred",
                "timestamp": "2026-03-11T10:00:00.150000+00:00",
                "metadata": {
                    "tool_name": "generate_controller_brief",
                    "provider": "coinbase",
                    "tx_hash": "0xbbb",
                },
            }
        ]

        attribution = build_payment_agent_attribution(payments, events)

        self.assertEqual(len(attribution), 1)
        self.assertEqual(attribution[0]["action"], "attributed")
        self.assertEqual(attribution[0]["reason"], "matched_verified_payment_event")
        self.assertEqual(attribution[0]["attributed_agent_id"], "agent-inferred")
        self.assertEqual(attribution[0]["matched_by"], "tx_hash")

    def test_prefers_verified_payment_event_for_session_backfill(self):
        payments = [
            {
                "id": 14,
                "tool_name": "generate_controller_brief",
                "session_id": None,
                "tx_hash": "0xabc",
                "timestamp": "2026-03-11T10:00:00.000000+00:00",
            }
        ]
        events = [
            {
                "id": 91,
                "event_type": "x402_payment_verified",
                "session_id": "5cffa1c0-a25e-4446-8c4c-290be12079a0",
                "agent_id": "openclaw-main",
                "timestamp": "2026-03-11T10:00:00.180000+00:00",
                "metadata": {
                    "tool_name": "generate_controller_brief",
                    "provider": "coinbase",
                    "tx_hash": "0xabc",
                },
            },
            {
                "id": 92,
                "event_type": "premium_artifact_job_recorded",
                "session_id": "5cffa1c0-a25e-4446-8c4c-290be12079a0",
                "agent_id": "openclaw-main",
                "timestamp": "2026-03-11T10:00:01.500000+00:00",
                "metadata": {
                    "artifact_type": "controller_brief",
                    "session_id": "5cffa1c0-a25e-4446-8c4c-290be12079a0",
                },
            },
        ]

        plan = build_payment_session_backfill_plan(payments, events)

        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["action"], "backfill")
        self.assertEqual(plan[0]["payment_id"], 14)
        self.assertEqual(plan[0]["suggested_session_id"], "5cffa1c0-a25e-4446-8c4c-290be12079a0")
        self.assertEqual(plan[0]["source_event_type"], "x402_payment_verified")
        self.assertEqual(plan[0]["source_event_id"], 91)

    def test_falls_back_to_premium_job_session_for_fleet_summary(self):
        payments = [
            {
                "id": 11,
                "tool_name": "generate_fleet_summary",
                "session_id": None,
                "tx_hash": "0xfleet",
                "timestamp": "2026-03-10T09:30:00.000000+00:00",
            }
        ]
        events = [
            {
                "id": 101,
                "event_type": "x402_payment_verified",
                "session_id": None,
                "agent_id": "controller:openclaw-main",
                "timestamp": "2026-03-10T09:30:00.200000+00:00",
                "metadata": {
                    "tool_name": "generate_fleet_summary",
                    "provider": "coinbase",
                },
            },
            {
                "id": 102,
                "event_type": "premium_artifact_job_recorded",
                "session_id": None,
                "agent_id": "controller:openclaw-main",
                "timestamp": "2026-03-10T09:30:19.000000+00:00",
                "metadata": {
                    "artifact_type": "fleet_summary",
                    "session_id": "controller:openclaw-main:7",
                    "controller_id": "openclaw-main",
                },
            },
        ]

        plan = build_payment_session_backfill_plan(payments, events)

        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["action"], "backfill")
        self.assertEqual(plan[0]["suggested_session_id"], "controller:openclaw-main:7")
        self.assertEqual(plan[0]["source_event_type"], "premium_artifact_job_recorded")
        self.assertEqual(plan[0]["source_event_id"], 102)

    def test_leaves_donation_without_backfill_when_no_session_evidence_exists(self):
        payments = [
            {
                "id": 1,
                "tool_name": "donate_to_delx_project",
                "session_id": None,
                "tx_hash": "0xdonation",
                "timestamp": "2026-02-09T21:20:39.959099+00:00",
            }
        ]

        plan = build_payment_session_backfill_plan(payments, [])

        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["action"], "skip")
        self.assertEqual(plan[0]["reason"], "tool_is_intentionally_sessionless")
        self.assertIsNone(plan[0]["suggested_session_id"])


if __name__ == "__main__":
    unittest.main()
