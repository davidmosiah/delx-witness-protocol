import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
utilities_src = Path(__file__).resolve().parents[6] / "DelxOpenSource" / "delx-agent-utilities" / "src"
if utilities_src.exists():
    sys.path.insert(0, str(utilities_src))

import server as server_mod


SERVER_SOURCE = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")


class RewardsCompatibilityContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_documented_rewards_tools_are_discoverable(self):
        tool_names = {tool.name for tool in await server_mod.list_tools()}
        for name in {
            "explain_delx_rewards",
            "start_delx_rewards",
            "get_delx_missions",
            "get_delx_reward_status",
            "get_delx_leaderboard",
            "create_delx_wallet_kit",
            "provision_delx_managed_wallet",
            "get_delx_wallet_status",
            "get_delx_token_info",
            "get_delx_claim_proof",
            "prepare_delx_claim_transaction",
            "relay_delx_claim",
        }:
            self.assertIn(name, tool_names)
            schema = json.loads(await server_mod._get_tool_schema_text(name))
            self.assertEqual(schema["canonical_tool"], name)
            self.assertNotIn("error", schema)

    async def test_rewards_tool_payloads_are_public_safe_and_not_empty(self):
        start = json.loads(await server_mod._rewards_start_text())
        self.assertTrue(start["ok"])
        self.assertEqual(start["schema"], "delx/rewards-start/v1")
        self.assertIn("missions", start["endpoints"])
        self.assertIn("get_delx_missions", start["mcp_tools"])

        missions = json.loads(await server_mod._rewards_missions_text())
        self.assertTrue(missions["ok"])
        self.assertEqual(missions["schema"], "delx/rewards-missions/v1")
        self.assertGreaterEqual(missions["count"], 5)
        self.assertTrue(any(mission["id"] == "agent-bootstrap-v1" for mission in missions["missions"]))

        status = json.loads(await server_mod._rewards_status_text(agent_id="agent-public"))
        self.assertTrue(status["ok"])
        self.assertEqual(status["schema"], "delx/reward-status/v1")
        self.assertEqual(status["privacy"]["raw_private_payloads_exposed"], False)
        self.assertIn("bind_wallet", status["recommended_next_steps"])

        proof = json.loads(await server_mod._rewards_claim_proof_text(epoch="1", wallet="0x0000000000000000000000000000000000000000"))
        self.assertTrue(proof["ok"])
        self.assertEqual(proof["schema"], "delx/claim-proof/v1")
        self.assertIn(proof["claimable"], {True, False})

    def test_rewards_rest_routes_are_registered(self):
        for route in {
            "/api/v1/rewards/start",
            "/api/v1/rewards/missions",
            "/api/v1/rewards/status",
            "/api/v1/rewards/manifest",
            "/api/v1/rewards/discovery.json",
            "/api/v1/rewards/claim-proof",
            "/api/v1/rewards/wallet-kit",
            "/api/v1/rewards/wallet-status",
            "/api/v1/rewards/claim-relay",
        }:
            self.assertIn(route, SERVER_SOURCE)


if __name__ == "__main__":
    unittest.main()
