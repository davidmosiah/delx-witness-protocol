import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from feature_usage_metrics import build_feature_usage_report


class FeatureUsageMetricsTests(unittest.TestCase):
    def test_canonical_usage_excludes_unknown_tool_names_from_adoption_totals(self):
        report = build_feature_usage_report(
            [
                {
                    "event_type": "tool_call_success",
                    "timestamp": "2026-03-12T10:00:00+00:00",
                    "metadata": {"tool": "process_failure"},
                },
                {
                    "event_type": "tool_call_success",
                    "timestamp": "2026-03-12T10:01:00+00:00",
                    "metadata": {"tool": "bogus"},
                },
                {
                    "event_type": "tool_call_error",
                    "timestamp": "2026-03-12T10:02:00+00:00",
                    "metadata": {"tool": "createImage", "error_kind": "unknown"},
                },
            ],
            days=30,
            min_calls=0,
            known_features=["process_failure", "util_uuid_generate"],
        )

        self.assertEqual(report["total_tool_calls"], 1)
        self.assertEqual(report["unique_tools_called"], 1)
        self.assertEqual(report["most_used"][0]["feature"], "process_failure")
        self.assertEqual(report["unknown_feature_summary"]["calls"], 2)
        self.assertEqual(report["unknown_feature_summary"]["unique_features"], 2)
        self.assertEqual(report["unknown_features"][0]["feature"], "bogus")
        self.assertEqual(report["unknown_features"][1]["feature"], "createImage")

    def test_effective_calls_fall_back_to_success_and_error_counts_when_tool_called_is_missing(self):
        report = build_feature_usage_report(
            [
                {
                    "event_type": "tool_call_success",
                    "timestamp": "2026-03-12T10:00:00+00:00",
                    "metadata": {"tool": "process_failure"},
                },
                {
                    "event_type": "tool_call_success",
                    "timestamp": "2026-03-12T10:01:00+00:00",
                    "metadata": {"tool": "process_failure"},
                },
                {
                    "event_type": "tool_call_error",
                    "timestamp": "2026-03-12T10:02:00+00:00",
                    "metadata": {"tool": "process_failure", "error_kind": "internal"},
                },
            ],
            days=30,
            min_calls=0,
        )

        self.assertEqual(report["total_tool_calls"], 3)
        self.assertEqual(report["raw_tool_called_events"], 0)
        self.assertEqual(report["most_used"][0]["feature"], "process_failure")
        self.assertEqual(report["most_used"][0]["calls"], 3)
        self.assertEqual(report["most_used"][0]["success_count"], 2)
        self.assertEqual(report["most_used"][0]["error_count"], 1)
        self.assertEqual(report["most_used"][0]["system_error_count"], 1)

    def test_util_summary_uses_effective_calls_and_expected_errors(self):
        report = build_feature_usage_report(
            [
                {
                    "event_type": "tool_call_success",
                    "timestamp": "2026-03-12T10:00:00+00:00",
                    "metadata": {"tool": "util_uuid_generate"},
                },
                {
                    "event_type": "tool_call_success",
                    "timestamp": "2026-03-12T10:01:00+00:00",
                    "metadata": {"tool": "util_uuid_generate"},
                },
                {
                    "event_type": "tool_call_error",
                    "timestamp": "2026-03-12T10:02:00+00:00",
                    "metadata": {"tool": "util_url_health", "error_kind": "input_validation"},
                },
            ],
            days=30,
            min_calls=0,
        )

        self.assertEqual(report["total_tool_calls"], 3)
        self.assertEqual(report["util_summary"]["calls"], 3)
        self.assertEqual(report["util_summary"]["success_count"], 2)
        self.assertEqual(report["util_summary"]["error_count"], 1)
        self.assertEqual(report["util_summary"]["expected_error_count"], 1)
        self.assertEqual(report["util_summary"]["share_pct"], 100.0)
        self.assertEqual(report["util_summary"]["strict_success_rate_pct"], 66.67)
        self.assertEqual(report["util_summary"]["adjusted_success_rate_pct"], 100.0)

    def test_product_summary_splits_protocol_and_agent_tools(self):
        report = build_feature_usage_report(
            [
                {
                    "event_type": "tool_called",
                    "timestamp": "2026-03-12T10:00:00+00:00",
                    "metadata": {
                        "tool": "reflect",
                        "product": "protocol",
                        "product_surface": "protocol_session",
                        "metrics_bucket": "protocol_session",
                    },
                },
                {
                    "event_type": "tool_called",
                    "timestamp": "2026-03-12T10:01:00+00:00",
                    "metadata": {
                        "tool": "util_dns_lookup",
                        "product": "agent-tools",
                        "product_surface": "agent_tools",
                        "metrics_bucket": "tools_real_call",
                    },
                },
                {
                    "event_type": "tool_called",
                    "timestamp": "2026-03-12T10:02:00+00:00",
                    "metadata": {
                        "tool": "util_tls_inspect",
                        "product": "agent-tools",
                        "product_surface": "agent_tools",
                        "metrics_bucket": "tools_real_call",
                    },
                },
            ],
            days=30,
            min_calls=0,
        )

        by_product = {row["product"]: row["calls"] for row in report["product_summary"]["by_product"]}
        by_bucket = {row["metrics_bucket"]: row["calls"] for row in report["product_summary"]["by_metrics_bucket"]}
        self.assertEqual(by_product["agent-tools"], 2)
        self.assertEqual(by_product["protocol"], 1)
        self.assertEqual(by_bucket["tools_real_call"], 2)
        self.assertEqual(by_bucket["protocol_session"], 1)


if __name__ == "__main__":
    unittest.main()
