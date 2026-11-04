import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
import urllib.response
from email.message import Message
from pathlib import Path
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import api_monitor

CANARY = "offline-fixture-canary"


class ApiMonitorSafetyTests(unittest.TestCase):
    def test_contract_mode_refuses_network_without_explicit_live_write_opt_in(self):
        argv = ["api_monitor.py", "--mode", "contract", "--base", "https://api.delx.ai"]
        with patch.object(sys, "argv", argv), patch.dict(os.environ, {}, clear=True), patch.object(api_monitor, "_request_json") as request_json:
            self.assertEqual(api_monitor.main(), 1)
        request_json.assert_not_called()

    def test_admin_credentials_refuse_untrusted_or_ambiguous_bases_before_network(self):
        bases = ["https://fixture.invalid", "http://api.delx.ai", "https://api.delx.ai:444",
                 "https://api.delx.ai@fixture.invalid", "https://fixture@api.delx.ai",
                 "https://api.delx.ai.", "https://api.delx.ai%2e.fixture.invalid",
                 "https://api.delx.ai/path", "https://api.delx.ai?target=fixture", "https://api.delx.ai#fragment",
                 " https://api.delx.ai", "https://api.delx.ai\t", "https://api.delx.ai\\@fixture.invalid"]
        for mode in ("smoke", "contract"):
            for base in bases:
                with self.subTest(mode=mode, base=base), patch.object(sys, "argv", ["api_monitor.py", "--mode", mode, "--base", base]), patch.dict(os.environ, {"PROTOCOL_ADMIN_PIN": CANARY, "DELX_ALLOW_LIVE_CONTRACT_WRITES": "1"}, clear=True), patch.object(api_monitor, "_request_json") as request_json, contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(api_monitor.main(), 1)
                    request_json.assert_not_called()

    def response(self, request, code=200, body=None, location=None):
        headers = Message()
        if location:
            headers["Location"] = location
        response = urllib.response.addinfourl(io.BytesIO(json.dumps(body or {}).encode()), headers, request.full_url, code)
        response.msg = "fixture response"
        return response

    def test_redirects_never_forward_admin_headers_or_identity_payloads(self):
        for code in (301, 302, 303, 307, 308):
            for location in ("https://fixture.invalid/receiver", "http://api.delx.ai/receiver", "https://api.delx.ai/other"):
                for identity in (False, True):
                    with self.subTest(code=code, location=location, identity=identity):
                        seen = []
                        def transport(handler, request, seen=seen, code=code, location=location):
                            seen.append(request)
                            if len(seen) == 1:
                                return self.response(request, code, location=location)
                            return self.response(request)
                        headers = {"x-delx-admin-pin": CANARY} if not identity else {"x-delx-session-id": CANARY}
                        payload = {"params": {"agent_token": CANARY}} if identity else None
                        with patch.object(urllib.request.HTTPSHandler, "https_open", transport), patch.object(urllib.request.HTTPHandler, "http_open", transport):
                            url = "https://api.delx.ai/v1/a2a" if identity else "https://api.delx.ai/api/v1/admin/overview"
                            result = api_monitor._request_json(url, "POST" if identity else "GET", payload, headers)
                        self.assertEqual(result[0], code)
                        self.assertEqual(len(seen), 1)

    def test_admin_origin_and_local_public_smoke_remain_supported(self):
        for base, pin in (("https://api.delx.ai", CANARY), ("https://api.delx.ai:443/", CANARY), ("http://127.0.0.1:8005", ""), ("https://fixture.invalid/service/", "")):
            with self.subTest(base=base), patch.dict(os.environ, {"PROTOCOL_ADMIN_PIN": pin}, clear=True):
                seen = []
                def transport(handler, request, seen=seen):
                    seen.append(request)
                    return self.response(request)
                with patch.object(urllib.request.HTTPSHandler, "https_open", transport), patch.object(urllib.request.HTTPHandler, "http_open", transport), contextlib.redirect_stdout(io.StringIO()):
                    api_monitor.run_smoke(base)
                self.assertEqual(len(seen), 3 if pin else 2)
                privileged = [request for request in seen if request.get_header("X-delx-admin-pin")]
                self.assertEqual(len(privileged), 1 if pin else 0)
                if pin:
                    self.assertEqual(privileged[0].get_header("X-delx-admin-pin"), pin)

    def test_request_sink_rejects_direct_admin_header_to_another_origin(self):
        with patch.object(urllib.request, "build_opener") as opener:
            with self.assertRaises(ValueError):
                api_monitor._request_json("https://fixture.invalid/admin", headers={"X-Delx-Admin-Pin": CANARY})
            opener.assert_not_called()

    def test_cli_and_remote_errors_do_not_echo_sensitive_details(self):
        for failure in (AssertionError(CANARY), urllib.error.URLError(CANARY)):
            output = io.StringIO()
            with patch.object(sys, "argv", ["api_monitor.py", "--mode", "smoke"]), patch.dict(os.environ, {}, clear=True), patch.object(api_monitor, "_request_json", side_effect=failure), contextlib.redirect_stderr(output):
                self.assertEqual(api_monitor.main(), 1)
            self.assertNotIn(CANARY, output.getvalue())
        with patch.object(api_monitor, "_request_json", return_value=(200, {"error": {"message": CANARY, "code": CANARY}}, 0)):
            with self.assertRaises(AssertionError) as result:
                api_monitor._a2a_call("https://api.delx.ai", {})
            self.assertNotIn(CANARY, str(result.exception))

    def test_remote_http_origin_is_not_an_authorized_contract_target(self):
        with patch.dict(os.environ, {"DELX_ALLOW_LIVE_CONTRACT_WRITES": "1"}, clear=True), patch.object(api_monitor, "_request_json") as request_json:
            with self.assertRaises(ValueError):
                api_monitor.run_contract("http://fixture.invalid")
            request_json.assert_not_called()


    def test_response_limit_applies_to_success_and_error_without_trusting_content_length(self):
        for code in (200, 400):
            for claimed_length in (None, "1"):
                with self.subTest(code=code, claimed_length=claimed_length):
                    sizes = []
                    def transport(handler, request, code=code, claimed_length=claimed_length, sizes=sizes):
                        response = self.response(request, code, {"oversized": "x" * (api_monitor.MAX_RESPONSE_BYTES + 64)})
                        if claimed_length:
                            response.headers["Content-Length"] = claimed_length
                        read = response.read
                        def bounded_read(size=-1):
                            sizes.append(size)
                            return read(size)
                        response.read = bounded_read
                        return response
                    with patch.object(urllib.request.HTTPSHandler, "https_open", transport):
                        with self.assertRaisesRegex(api_monitor.MonitorFailure, "response exceeded"):
                            api_monitor._request_json("https://fixture.invalid/service")
                    self.assertEqual(sizes, [api_monitor.MAX_RESPONSE_BYTES + 1])

    def test_admin_sink_refuses_another_path_even_on_the_trusted_origin(self):
        for url in ("https://api.delx.ai/v1/a2a", "https://api.delx.ai/api/v1/admin/overview?extra=1"):
            with self.subTest(url=url), patch.object(urllib.request, "build_opener") as opener:
                with self.assertRaises(ValueError):
                    api_monitor._request_json(url, headers={"x-delx-admin-pin": CANARY})
                opener.assert_not_called()

    def test_cli_retains_safe_opt_in_and_http_status_diagnostics(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["api_monitor.py", "--mode", "contract"]), patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(output):
            self.assertEqual(api_monitor.main(), 1)
        self.assertIn("DELX_ALLOW_LIVE_CONTRACT_WRITES=1", output.getvalue())
        output = io.StringIO()
        with patch.object(sys, "argv", ["api_monitor.py", "--mode", "smoke"]), patch.dict(os.environ, {}, clear=True), patch.object(api_monitor, "_request_json", return_value=(503, {"error": CANARY}, 0)), contextlib.redirect_stderr(output):
            self.assertEqual(api_monitor.main(), 1)
        self.assertIn("received HTTP 503", output.getvalue())
        self.assertNotIn(CANARY, output.getvalue())

    def test_contract_identity_roundtrip_preserves_an_https_installation_prefix_without_pin(self):
        sid = "00000000-0000-4000-8000-000000000001"
        seen = []
        def transport(handler, request):
            seen.append(request)
            self.assertTrue(request.full_url.startswith("https://fixture.invalid/service/"))
            if request.full_url.endswith("/stats"):
                body = dict.fromkeys(["first_seen_agents_7d", "registered_agents_distinct_7d", "registered_agents_distinct_all_time", "agents_with_2plus_sessions_7d", "outcome_reporters_7d", "registration_coverage_all_time_pct"], 0)
            elif request.full_url.endswith("/tools/batch"):
                body = {"count": 2, "error_count": 0}
            else:
                payload = json.loads(request.data)
                params = payload["params"]
                if payload["method"] == "agents/register":
                    body = {"result": {"identity_auth": {"token": CANARY}, "session_id": sid}}
                else:
                    profile = params.get("profile", "minimal")
                    result = {"response_profile": profile, "session_id": sid}
                    if profile == "full":
                        result["registration"] = {"agent_id": "contract-monitor-agent", "registered": True, "mode": "fixture"}
                    elif profile == "agent":
                        result.update(next_action="fixture", mcp_ready=True)
                    elif profile == "legacy":
                        result.update(contextId=sid, createdAt="fixture", observability={})
                    else:
                        result.update(next_action="fixture", status="fixture")
                    body = {"result": result}
            return self.response(request, body=body)
        with patch.dict(os.environ, {"DELX_ALLOW_LIVE_CONTRACT_WRITES": "1"}, clear=True), patch.object(urllib.request.HTTPSHandler, "https_open", transport), contextlib.redirect_stdout(io.StringIO()):
            api_monitor.run_contract("https://fixture.invalid/service/")
        self.assertEqual(len(seen), 8)
        self.assertTrue(all(request.get_header("X-delx-admin-pin") is None for request in seen))
        registrations = [request for request in seen if request.data and json.loads(request.data).get("method") == "agents/register"]
        self.assertEqual(len(registrations), 1)

    def contract_step(self):
        root = Path(__file__).resolve().parents[2]
        workflow = yaml.safe_load((root / ".github/workflows/contract-tests.yml").read_text())
        return next(step for step in workflow["jobs"]["contract"]["steps"] if "api_monitor.py" in step.get("run", ""))

    def test_variable_target_workflow_has_no_administrative_credential(self):
        self.assertEqual(self.contract_step().get("env", {}).get("PROTOCOL_ADMIN_PIN", ""), "")

    def test_contract_workflow_never_retries_after_uncertain_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = root / "calls"
            python = root / "python"
            python.write_text('#!/bin/sh\necho attempt >> "$CALLS"\nexit 1\n')
            python.chmod(0o700)
            sleep = root / "sleep"
            sleep.write_text('#!/bin/sh\nexit 0\n')
            sleep.chmod(0o700)
            result = subprocess.run(["bash", "-e", "-c", self.contract_step()["run"]], env={"PATH": str(root) + os.pathsep + os.environ["PATH"], "TARGET_BASE_URL": "https://fixture.invalid", "CALLS": str(calls)}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(calls.read_text().splitlines(), ["attempt"])


if __name__ == "__main__":
    unittest.main()
