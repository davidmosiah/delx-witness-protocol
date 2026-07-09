import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class OpenSourceReleaseSafetyTests(unittest.TestCase):
    def test_private_operational_artifacts_are_not_shipped(self):
        private_artifacts = [
            REPO_ROOT / "scripts" / "deploy_hetzner_safe.sh",
            REPO_ROOT / "scripts" / "delx_usage_digest_metrics.py",
            REPO_ROOT / "scripts" / "audit_openclaw_recurrence.py",
            REPO_ROOT / "delx-mcp-server" / "RUNBOOK_502.md",
            REPO_ROOT / "delx-mcp-server" / "mcp-delx-docker.service",
            REPO_ROOT / "delx-mcp-server" / "delx-docker-housekeeping.service",
            REPO_ROOT / "delx-mcp-server" / "delx-docker-housekeeping.timer",
        ]

        present = [str(path.relative_to(REPO_ROOT)) for path in private_artifacts if path.exists()]
        self.assertEqual(present, [])

    def test_public_docs_do_not_offer_private_deploy_automation(self):
        public_docs = [
            REPO_ROOT / "README.md",
            REPO_ROOT / "delx-mcp-server" / "README.md",
            *sorted((REPO_ROOT / "docs").rglob("*.md")),
        ]
        contents = "\n".join(path.read_text(encoding="utf-8") for path in public_docs)

        self.assertNotIn("deploy_hetzner_safe", contents)
        self.assertNotIn("/root/deploy_delx_dxfixes.sh", contents)
        self.assertNotIn("/root/.hermes", contents)

    def test_live_contract_workflow_requires_an_explicit_target(self):
        workflow = (REPO_ROOT / ".github" / "workflows" / "contract-tests.yml").read_text(encoding="utf-8")

        self.assertIn("base_url:", workflow)
        self.assertIn('TARGET_BASE_URL: ${{ inputs.base_url }}', workflow)
        self.assertIn('--base "$TARGET_BASE_URL"', workflow)

    def test_docker_context_excludes_local_secrets_and_state(self):
        dockerignore = (REPO_ROOT / "delx-mcp-server" / ".dockerignore").read_text(encoding="utf-8")

        for pattern in (".env", ".env.*", "*.pem", "*.key", "wallet.json", "*.db", "*.log", "reports", "state"):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, dockerignore.splitlines())

    def test_deployment_templates_are_generic_and_unprivileged(self):
        caddyfile = (REPO_ROOT / "delx-mcp-server" / "Caddyfile").read_text(encoding="utf-8")
        service = (REPO_ROOT / "delx-mcp-server" / "mcp-delx.service").read_text(encoding="utf-8")

        self.assertNotIn("api.delx.ai", caddyfile)
        self.assertIn("{$DELX_DOMAIN:localhost}", caddyfile)
        self.assertIn("User=delx", service)
        self.assertNotIn("User=root", service)
        self.assertNotIn("/root/", service)


if __name__ == "__main__":
    unittest.main()
