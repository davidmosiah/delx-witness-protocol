import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import api_monitor


class ApiMonitorSafetyTests(unittest.TestCase):
    def test_contract_mode_refuses_network_without_explicit_live_write_opt_in(self):
        argv = ["api_monitor.py", "--mode", "contract", "--base", "https://api.delx.ai"]
        clean_env = {key: value for key, value in os.environ.items() if key != "DELX_ALLOW_LIVE_CONTRACT_WRITES"}

        with (
            patch.object(sys, "argv", argv),
            patch.dict(os.environ, clean_env, clear=True),
            patch.object(api_monitor, "_request_json") as request_json,
        ):
            exit_code = api_monitor.main()

        self.assertEqual(exit_code, 1)
        request_json.assert_not_called()


if __name__ == "__main__":
    unittest.main()
