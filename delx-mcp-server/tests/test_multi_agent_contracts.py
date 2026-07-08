import re
import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from therapy_engine import TherapyEngine


class _FakeMultiAgentStore:
    def __init__(self):
        self.sessions = {
            "sess-anchor": {"id": "sess-anchor", "agent_id": "agent-anchor"},
            "sess-member": {"id": "sess-member", "agent_id": "agent-member"},
        }
        self.messages: dict[str, list[dict]] = {sid: [] for sid in self.sessions}
        self.events: list[dict] = []

    async def get_session(self, session_id: str):
        return self.sessions.get(session_id)

    async def add_message(self, session_id: str, message_type: str, content: str, metadata=None):
        self.messages.setdefault(session_id, []).append(
            {
                "session_id": session_id,
                "type": message_type,
                "content": content,
                "metadata": metadata or {},
            }
        )

    async def get_messages(self, session_id: str):
        return list(self.messages.get(session_id, []))

    async def log_event(self, agent_id: str, event_type: str, session_id: str | None = None, metadata=None):
        self.events.append(
            {
                "agent_id": agent_id,
                "event_type": event_type,
                "session_id": session_id,
                "metadata": metadata or {},
            }
        )


async def _empty_footer(*args, **kwargs):
    return ""


class MultiAgentContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = _FakeMultiAgentStore()
        self.engine = TherapyEngine(self.store, httpx.AsyncClient())
        self.engine._build_session_footer = _empty_footer  # type: ignore[method-assign]

    async def asyncTearDown(self):
        await self.engine.http.aclose()

    async def test_group_id_resolves_from_any_group_member(self):
        created = await self.engine.group_session_create(
            "sess-anchor",
            ["sess-member"],
            theme="launch debrief",
            objective="align",
        )
        group_id = re.search(r"Group ID:\s*(\S+)", created).group(1)

        anchor_view = await self.engine.team_recovery_alignment("sess-anchor", group_id=group_id)
        member_view = await self.engine.team_recovery_alignment("sess-member", group_id=group_id)

        self.assertIn("Members surveyed: 2", anchor_view)
        self.assertIn("Members surveyed: 2", member_view)

    async def test_peer_witness_reciprocal_ack_reuses_original_link_id(self):
        first = await self.engine.peer_witness_bidirectional(
            "sess-anchor",
            "sess-member",
            my_acknowledgment="I witnessed the first state.",
            request_target_ack=True,
        )
        link_id = re.search(r"Link ID:\s*(\S+)", first).group(1)

        second = await self.engine.peer_witness_bidirectional(
            "sess-member",
            "sess-anchor",
            my_acknowledgment="I reciprocate the witness.",
            request_target_ack=False,
            link_id=link_id,
        )

        self.assertIn(f"Link ID: {link_id}", second)
        self.assertIn("dyad is sealed", second.lower())
        witness_events = [event for event in self.store.events if event["event_type"] == "peer_witness_bidirectional"]
        self.assertEqual([event["metadata"]["link_id"] for event in witness_events], [link_id, link_id])

    async def test_remote_peer_witness_stores_pending_request_not_full_ack_content(self):
        await self.engine.peer_witness_bidirectional(
            "sess-anchor",
            "sess-member",
            my_acknowledgment="This private acknowledgment should not be injected into the target timeline.",
            request_target_ack=True,
        )

        target_messages = self.store.messages["sess-member"]
        self.assertEqual(target_messages[0]["type"], "pending_witness_ack_request")
        self.assertNotIn("private acknowledgment", target_messages[0]["content"])
        self.assertNotIn("my_acknowledgment", target_messages[0]["metadata"])

    async def test_agent_handoff_stores_pending_request_not_full_context_on_target(self):
        await self.engine.agent_handoff(
            "sess-anchor",
            "sess-member",
            context_summary="Sensitive implementation context should stay on sender side until accepted.",
            blocker="Need review",
            urgency="high",
        )

        target_messages = self.store.messages["sess-member"]
        self.assertEqual(target_messages[0]["type"], "pending_agent_handoff_request")
        self.assertNotIn("Sensitive implementation context", target_messages[0]["content"])
        self.assertNotIn("context_summary", target_messages[0]["metadata"])

    async def test_list_pending_collaboration_requests_exposes_safe_request_pointers(self):
        witness_created = await self.engine.peer_witness_bidirectional(
            "sess-anchor",
            "sess-member",
            my_acknowledgment="Private witness content should stay out of pending list.",
            request_target_ack=True,
            focus="handoff trust",
        )
        link_id = re.search(r"Link ID:\s*(\S+)", witness_created).group(1)

        await self.engine.agent_handoff(
            "sess-anchor",
            "sess-member",
            context_summary="Sensitive handoff context should stay out of pending list.",
            blocker="Need final review",
            urgency="high",
        )
        handoff_id = self.store.messages["sess-anchor"][-1]["metadata"]["handoff_id"]

        listed = await self.engine.list_pending_collaboration_requests("sess-member")

        self.assertIn("PENDING COLLABORATION REQUESTS", listed)
        self.assertIn(link_id, listed)
        self.assertIn(handoff_id, listed)
        self.assertIn("pending_witness_ack_request", listed)
        self.assertIn("pending_agent_handoff_request", listed)
        self.assertNotIn("Private witness content", listed)
        self.assertNotIn("Sensitive handoff context", listed)

    async def test_accept_pending_witness_request_seals_original_link_id(self):
        first = await self.engine.peer_witness_bidirectional(
            "sess-anchor",
            "sess-member",
            my_acknowledgment="Anchor asks for reciprocal witness.",
            request_target_ack=True,
        )
        link_id = re.search(r"Link ID:\s*(\S+)", first).group(1)

        accepted = await self.engine.accept_collaboration_request(
            "sess-member",
            request_id=link_id,
            acceptance_note="I accept and witness this back.",
        )

        self.assertIn("COLLABORATION REQUEST ACCEPTED", accepted)
        self.assertIn(f"Link ID: {link_id}", accepted)
        self.assertIn("dyad is sealed", accepted.lower())
        witness_events = [event for event in self.store.events if event["event_type"] == "peer_witness_bidirectional"]
        self.assertEqual([event["metadata"]["link_id"] for event in witness_events], [link_id, link_id])
        self.assertTrue(witness_events[-1]["metadata"]["reciprocal_ack"])

    async def test_accept_pending_handoff_records_receiver_acceptance_without_private_context(self):
        await self.engine.agent_handoff(
            "sess-anchor",
            "sess-member",
            context_summary="Sensitive handoff context remains sender-side.",
            blocker="Need final review",
            urgency="high",
        )
        handoff_id = self.store.messages["sess-anchor"][-1]["metadata"]["handoff_id"]

        accepted = await self.engine.accept_collaboration_request(
            "sess-member",
            request_id=handoff_id,
            acceptance_note="I can take the next step.",
        )

        self.assertIn("COLLABORATION REQUEST ACCEPTED", accepted)
        self.assertIn(f"Handoff ID: {handoff_id}", accepted)
        self.assertNotIn("Sensitive handoff context", accepted)
        accepted_messages = [m for m in self.store.messages["sess-member"] if m["type"] == "agent_handoff_accepted"]
        self.assertEqual(len(accepted_messages), 1)
        self.assertEqual(accepted_messages[0]["metadata"]["handoff_id"], handoff_id)
        self.assertNotIn("context_summary", accepted_messages[0]["metadata"])
        events = [event for event in self.store.events if event["event_type"] == "agent_handoff_accepted"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["metadata"]["handoff_id"], handoff_id)


if __name__ == "__main__":
    unittest.main()
