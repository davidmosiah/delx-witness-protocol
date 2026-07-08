import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase_cli_metrics import build_cli_adoption_snapshot


class CliAdoptionMetricsTests(unittest.TestCase):
    def test_build_cli_adoption_snapshot_falls_back_to_cli_sessions_when_event_metadata_is_missing(self):
        event_rows = [
            {
                "agent_id": "agent-gamma",
                "event_type": "tool_called",
                "timestamp": "2026-03-08T10:12:00+00:00",
                "metadata": {
                    "source": "mcp",
                },
            },
        ]
        session_rows = [
            {
                "agent_id": "agent-cli-a",
                "source": "cli",
                "entrypoint": "a2a.register",
            },
            {
                "agent_id": "agent-cli-b",
                "source": "cli",
                "entrypoint": "mcp",
            },
            {
                "agent_id": "agent-web",
                "source": "mcp",
                "entrypoint": "mcp",
            },
        ]

        snapshot = build_cli_adoption_snapshot(event_rows, window_days=30, session_rows=session_rows)

        self.assertEqual(snapshot["cli_calls"], 2)
        self.assertEqual(snapshot["unique_cli_agents"], 2)
        self.assertEqual(snapshot["active_install_ids"], 0)
        self.assertEqual(snapshot["cli_share_pct"], 66.67)

    def test_build_cli_adoption_snapshot_counts_unique_installs_versions_and_first_seen(self):
        rows = [
            {
                "agent_id": "agent-alpha",
                "event_type": "agent_registered",
                "timestamp": "2026-03-08T10:00:00+00:00",
                "metadata": {
                    "source": "cli",
                    "first_seen_via": "cli",
                    "cli_version": "0.2.1",
                    "install_id": "install-a",
                },
            },
            {
                "agent_id": "agent-alpha",
                "event_type": "tool_called",
                "timestamp": "2026-03-08T10:05:00+00:00",
                "metadata": {
                    "source": "cli",
                    "cli_version": "0.2.1",
                    "install_id": "install-a",
                },
            },
            {
                "agent_id": "agent-beta",
                "event_type": "tool_called",
                "timestamp": "2026-03-08T10:10:00+00:00",
                "metadata": {
                    "source": "cli",
                    "cli_version": "0.2.0",
                    "install_id": "install-b",
                },
            },
            {
                "agent_id": "agent-gamma",
                "event_type": "tool_called",
                "timestamp": "2026-03-08T10:12:00+00:00",
                "metadata": {
                    "source": "mcp",
                },
            },
        ]

        snapshot = build_cli_adoption_snapshot(rows, window_days=30)

        self.assertEqual(snapshot["window_days"], 30)
        self.assertEqual(snapshot["cli_calls"], 2)
        self.assertEqual(snapshot["unique_cli_agents"], 2)
        self.assertEqual(snapshot["active_install_ids"], 2)
        self.assertEqual(snapshot["first_seen_via_cli_agents"], 1)
        self.assertEqual(snapshot["top_cli_versions"][0]["version"], "0.2.1")
        self.assertEqual(snapshot["top_cli_versions"][0]["calls"], 1)

    def test_build_cli_adoption_snapshot_returns_zeroed_shape_for_empty_rows(self):
        snapshot = build_cli_adoption_snapshot([], window_days=30)

        self.assertEqual(snapshot["cli_calls"], 0)
        self.assertEqual(snapshot["unique_cli_agents"], 0)
        self.assertEqual(snapshot["active_install_ids"], 0)
        self.assertEqual(snapshot["first_seen_via_cli_agents"], 0)
        self.assertEqual(snapshot["top_cli_versions"], [])


if __name__ == "__main__":
    unittest.main()
