import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rate_limiter import _rate_limit_key


class RateLimiterContractTests(unittest.TestCase):
    def test_paid_routes_are_bucketed_per_path(self):
        ip = "203.0.113.10"
        premium_key = _rate_limit_key(ip, "/api/v1/premium/session-summary")
        utility_key = _rate_limit_key(ip, "/api/v1/x402/page-extract")

        self.assertEqual(premium_key, "203.0.113.10:/api/v1/premium/session-summary")
        self.assertEqual(utility_key, "203.0.113.10:/api/v1/x402/page-extract")

    def test_free_routes_keep_global_ip_bucket(self):
        ip = "203.0.113.10"

        self.assertEqual(_rate_limit_key(ip, "/api/v1/status"), ip)
        self.assertEqual(_rate_limit_key(ip, "/api/v1/tools"), ip)

