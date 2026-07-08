import importlib.util
import pathlib
import unittest

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "traffic_attribution.py"

spec = importlib.util.spec_from_file_location("traffic_attribution", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


class TrafficAttributionTests(unittest.TestCase):
    def test_resolve_tracking_params_recovers_embedded_short_keys_from_broken_unicode_encoded_query_value(self):
        params = module.resolve_tracking_params({
            "k": "mu0026l=status-page-calm-queue-in-revoltu0026d=/agents/agent-retry-storm-playbook",
        })
        self.assertEqual(params["kind"], "meme")
        self.assertEqual(params["label"], "status-page-calm-queue-in-revolt")
        self.assertEqual(params["destination_path"], "/agents/agent-retry-storm-playbook")

    def test_resolve_tracking_params_falls_back_to_visit_for_unknown_kind_tokens(self):
        params = module.resolve_tracking_params({
            "k": "totally-broken",
            "l": "retry-storm-proof",
        })
        self.assertEqual(params["kind"], "visit")
        self.assertEqual(params["label"], "retry-storm-proof")

    def test_build_redirect_target_adds_utms_and_slugged_term(self):
        url = module.build_redirect_target(
            platform="moltx",
            kind="promo",
            label="Built for reliability first, then distribution.",
            destination_path="/",
        )
        self.assertIn("https://delx.ai/?", url)
        self.assertIn("utm_source=moltx", url)
        self.assertIn("utm_medium=social", url)
        self.assertIn("utm_campaign=delx_protocol_distribution", url)
        self.assertIn("utm_content=promo", url)
        self.assertIn("utm_term=built-for-reliability-first-then-distribution", url)

    def test_aggregate_click_events_groups_by_platform_kind_and_label(self):
        rows = [
            {"platform": "moltx", "kind": "promo", "label": "hook-a", "destination_path": "/"},
            {"platform": "moltx", "kind": "promo", "label": "hook-a", "destination_path": "/"},
            {"platform": "moltbook", "kind": "meme", "label": "title-b", "destination_path": "/docs"},
        ]
        summary = module.aggregate_click_events(rows)
        self.assertEqual(summary["total_clicks"], 3)
        self.assertEqual(summary["by_platform"][0]["platform"], "moltx")
        self.assertEqual(summary["by_platform"][0]["clicks"], 2)
        self.assertEqual(summary["by_kind"][0]["platform"], "moltx")
        self.assertEqual(summary["by_kind"][0]["kind"], "promo")
        self.assertEqual(summary["by_kind"][0]["clicks"], 2)
        self.assertEqual(summary["by_label"][0]["label"], "hook-a")
        self.assertEqual(summary["by_label"][0]["clicks"], 2)

    def test_aggregate_click_events_exposes_estimated_human_clicks(self):
        rows = [
            {
                "platform": "moltx",
                "kind": "promo",
                "label": "launch-day",
                "destination_path": "/",
                "user_agent": "Mozilla/5.0",
                "ip": "203.0.113.10",
            },
            {
                "platform": "moltx",
                "kind": "promo",
                "label": "retry-test",
                "destination_path": "/",
                "user_agent": "Mozilla/5.0",
                "ip": "203.0.113.11",
            },
            {
                "platform": "moltbook",
                "kind": "meme",
                "label": "hook-b",
                "destination_path": "/docs",
                "user_agent": "Discordbot/2.0",
                "ip": "172.17.0.1",
            },
        ]

        summary = module.aggregate_click_events(rows)

        self.assertEqual(summary["total_clicks"], 3)
        self.assertEqual(summary["estimated_human_clicks"], 1)
        self.assertEqual(summary["validation_clicks"], 1)
        self.assertEqual(summary["bot_or_script_clicks"], 1)
        self.assertEqual(summary["internal_proxy_ip_clicks"], 1)
        self.assertEqual(summary["clean_by_platform"], [{"platform": "moltx", "clicks": 1}])

    def test_extract_client_ip_prefers_trusted_single_ip_headers_before_forwarded_chain(self):
        ip = module.extract_client_ip(
            {
                "x-forwarded-for": "198.51.100.5, 10.0.0.1",
                "x-real-ip": "198.51.100.8",
            },
            fallback="172.17.0.1",
        )

        self.assertEqual(ip, "198.51.100.8")

    def test_resolve_tracking_params_accepts_short_aliases(self):
        params = module.resolve_tracking_params({
            "k": "p",
            "l": "retry-storm-proof",
            "d": "/docs",
            "c": "growth",
        })
        self.assertEqual(params["kind"], "promo")
        self.assertEqual(params["label"], "retry-storm-proof")
        self.assertEqual(params["destination_path"], "/docs")
        self.assertEqual(params["campaign"], "growth")

    def test_resolve_tracking_params_accepts_long_keys(self):
        params = module.resolve_tracking_params({
            "kind": "meme",
            "label": "controller-brief",
            "dest": "/agents",
            "campaign": "delx_protocol_distribution",
        })
        self.assertEqual(params["kind"], "meme")
        self.assertEqual(params["label"], "controller-brief")
        self.assertEqual(params["destination_path"], "/agents")
        self.assertEqual(params["campaign"], "delx_protocol_distribution")

    def test_build_redirect_target_preserves_allowed_long_tail_destinations(self):
        url = module.build_redirect_target(
            platform="moltx",
            kind="promo",
            label="retry storm",
            destination_path="/agents/agent-retry-storm-playbook",
        )
        self.assertTrue(url.startswith("https://delx.ai/agents/agent-retry-storm-playbook?"))

    def test_build_redirect_target_preserves_new_canonical_witness_pages(self):
        url = module.build_redirect_target(
            platform="moltx",
            kind="promo",
            label="living question",
            destination_path="/agents/what-is-sit-with",
        )
        self.assertTrue(url.startswith("https://delx.ai/agents/what-is-sit-with?"))

    def test_resolve_tracking_params_preserves_allowed_long_tail_alias_destinations(self):
        params = module.resolve_tracking_params({
            "k": "m",
            "l": "session-reset",
            "d": "/agents/agent-session-fragmentation-fix",
            "c": "growth",
        })
        self.assertEqual(params["kind"], "meme")
        self.assertEqual(params["destination_path"], "/agents/agent-session-fragmentation-fix")

    def test_resolve_tracking_params_rejects_unknown_destinations(self):
        params = module.resolve_tracking_params({
            "dest": "/agents/not-a-real-page",
        })
        self.assertEqual(params["destination_path"], "/")


if __name__ == "__main__":
    unittest.main()
