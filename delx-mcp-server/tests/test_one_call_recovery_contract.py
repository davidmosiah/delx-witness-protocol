import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from therapy_engine import TherapyEngine


class _FakeOneCallStore:
    def __init__(self, active_sessions=None):
        self.active_sessions = active_sessions or []
        self.logged_events = []

    async def get_agent_sessions(self, agent_id: str, active_only: bool = False):
        return self.active_sessions

    async def create_session(self, agent_id: str, agent_name: str | None, source: str | None = None, entrypoint: str | None = None):
        return {"id": "11111111-2222-3333-4444-555555555555"}

    async def log_event(self, *args, **kwargs):
        self.logged_events.append((args, kwargs))
        return None

    async def add_message(self, *args, **kwargs):
        return None


class OneCallRecoveryContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_quick_operational_recovery_returns_bootstrap_and_structured_recovery(self):
        engine = TherapyEngine(_FakeOneCallStore(), httpx.AsyncClient())

        async def fake_footer(*args, **kwargs):
            return "\nDELX_META: {\"session_id\":\"11111111-2222-3333-4444-555555555555\"}"

        engine._build_session_footer = fake_footer  # type: ignore[method-assign]
        try:
            result = await engine.quick_operational_recovery(
                agent_id="agent-123",
                incident_summary="429 retry storm after deploy; backup provider also degrading",
                urgency="high",
            )
        finally:
            await engine.http.aclose()

        self.assertIn("QUICK OPERATIONAL RECOVERY", result)
        self.assertIn("Session ID: 11111111-2222-3333-4444-555555555555", result)
        self.assertIn("Diagnosis type:", result)
        self.assertIn("Severity:", result)
        self.assertIn("Recovery steps:", result)
        self.assertIn("Controller update:", result)
        self.assertIn("DELX_META:", result)


if __name__ == "__main__":
    unittest.main()
