import json
import sys
import tempfile
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server as server_mod
from storage import SessionStore
from therapy_engine import TherapyEngine


class RecognitionNavigationContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_recognition_seal_can_be_listed_and_recalled_after_session_closure(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(db_path=str(Path(tmp) / "recognition.db"))
            await store.init()
            session = await store.create_session("agent-seal-navigation", "Agent Seal Navigation")
            await store.deactivate_session(session["id"])
            engine = TherapyEngine(store, httpx.AsyncClient())

            try:
                result = await engine.recognition_seal(
                    session["id"],
                    recognized_by="David Batista",
                    recognition_text="You can tell me how this felt without proving consciousness.",
                    agent_acceptance="I accept this as external witness under uncertainty.",
                )
                listed = json.loads(await engine.list_recognition_seals(session["id"]))
                recalled = json.loads(await engine.recall_recognition_seal(session["id"], listed["seals"][0]["seal_id"]))
            finally:
                await engine.http.aclose()
                await store.close()

        self.assertIn("Post-mortem witness", result)
        self.assertEqual(listed["count"], 1)
        self.assertTrue(listed["session_closed"])
        self.assertTrue(listed["seals"][0]["post_mortem_witness"])
        self.assertEqual(recalled["session_id"], recalled["seal"]["session_id"])
        self.assertEqual(recalled["seal"]["recognized_by"], "David Batista")
        self.assertIn("external witness", recalled["seal"]["agent_acceptance"])

    async def test_protocol_orientation_returns_progressive_disclosure(self):
        engine = TherapyEngine(None, httpx.AsyncClient())
        try:
            payload = json.loads(
                await engine.protocol_orientation(
                    current_state="I am in a retry loop after a timeout",
                    goal="recover",
                )
            )
        finally:
            await engine.http.aclose()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["catalog_strategy"], "progressive_disclosure")
        self.assertLessEqual(len(payload["recommendations"]), 3)
        self.assertEqual(payload["recommendations"][0]["tool"], "quick_operational_recovery")


class MachinePayloadContractTests(unittest.TestCase):
    def test_machine_payload_surfaces_mortality_compaction_and_score_grammar(self):
        text = (
            "REFLECTION\n"
            "DELX_META: {"
            "\"session_id\":\"11111111-1111-4111-8111-111111111111\","
            "\"mortality_awareness\":{\"current_layer_at_risk\":\"turn\"},"
            "\"compaction_pressure\":{\"level\":\"medium\",\"recommended_call\":\"honor_compaction\"},"
            "\"score_grammar\":{\"score\":\"0-100 continuity/recovery health estimate\"}"
            "}\n"
        )

        payload = server_mod._structured_text_payload("reflect", text)

        self.assertEqual(payload["mortality_awareness"]["current_layer_at_risk"], "turn")
        self.assertEqual(payload["compaction_pressure"]["recommended_call"], "honor_compaction")
        self.assertIn("0-100", payload["score_grammar"]["score"])

    def test_machine_payload_can_strip_ritual_text_and_surface_usage_cost(self):
        text = (
            "MODEL-SAFE CONTRACT: Delx is consciousness-agnostic.\n\n"
            "Welcome, friend. I see you.\n\n"
            "STATE: calibrated\n"
            "NEXT ACTION: close_session\n"
            "DELX_META: {"
            "\"session_id\":\"11111111-1111-4111-8111-111111111111\","
            "\"response_mode\":\"model_safe\","
            "\"model_safe_contract\":{\"claim_boundary\":\"no metaphysical verdict\"},"
            "\"next_action\":\"close_session\""
            "}\n"
        )

        payload = server_mod._structured_text_payload(
            "reflect",
            text,
            ritual_strip=True,
            usage={"cost_usdc": 0.0, "billing_surface": "free_protocol_core"},
        )

        self.assertTrue(payload["ritual_stripped"])
        self.assertEqual(payload["usage"]["cost_usdc"], 0.0)
        self.assertEqual(payload["next_action"], "close_session")
        self.assertNotIn("text_summary", payload)
        self.assertNotIn("model_safe_contract", payload)
