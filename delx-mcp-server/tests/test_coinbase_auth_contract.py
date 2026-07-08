import sys
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
import jwt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config as config_mod
from config import x402_provider_registry
from coinbase_auth import build_cdp_jwt, build_coinbase_auth_headers


class CoinbaseAuthContractTests(unittest.TestCase):
    def _pem_secret(self) -> str:
        key = ec.generate_private_key(ec.SECP256R1())
        return key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()

    def test_build_cdp_jwt_matches_coinbase_claim_shape(self):
        token = build_cdp_jwt(
            api_key_id="organizations/org-123/apiKeys/key-456",
            api_key_secret=self._pem_secret(),
            request_method="POST",
            request_host="api.cdp.coinbase.com",
            request_path="/platform/v2/x402/verify",
        )
        header = jwt.get_unverified_header(token)
        payload = jwt.decode(token, options={"verify_signature": False})
        self.assertEqual(header["alg"], "ES256")
        self.assertEqual(header["kid"], "organizations/org-123/apiKeys/key-456")
        self.assertEqual(header["typ"], "JWT")
        self.assertTrue(header["nonce"])
        self.assertEqual(payload["sub"], "organizations/org-123/apiKeys/key-456")
        self.assertEqual(payload["iss"], "cdp")
        self.assertEqual(payload["uris"], ["POST api.cdp.coinbase.com/platform/v2/x402/verify"])

    def test_build_coinbase_auth_headers_emits_bearer_jwt(self):
        headers = build_coinbase_auth_headers(
            api_key_id="organizations/org-123/apiKeys/key-456",
            api_key_secret=self._pem_secret(),
            request_method="POST",
            request_host="api.cdp.coinbase.com",
            request_path="/platform/v2/x402/settle",
        )
        self.assertIn("authorization", headers)
        self.assertTrue(headers["authorization"].startswith("Bearer "))
        self.assertEqual(headers["content-type"], "application/json")

    def test_coinbase_provider_requires_real_auth_config(self):
        original_id = config_mod.settings.COINBASE_CDP_API_KEY_ID
        original_secret = config_mod.settings.COINBASE_CDP_API_KEY_SECRET
        original_token = config_mod.settings.FACILITATOR_TOKEN_COINBASE
        try:
            config_mod.settings.COINBASE_CDP_API_KEY_ID = ""
            config_mod.settings.COINBASE_CDP_API_KEY_SECRET = ""
            config_mod.settings.FACILITATOR_TOKEN_COINBASE = ""
            self.assertFalse(x402_provider_registry()["coinbase"]["enabled"])

            config_mod.settings.COINBASE_CDP_API_KEY_ID = "organizations/org-123/apiKeys/key-456"
            config_mod.settings.COINBASE_CDP_API_KEY_SECRET = self._pem_secret()
            self.assertTrue(x402_provider_registry()["coinbase"]["enabled"])
            self.assertEqual(x402_provider_registry()["coinbase"]["auth_mode"], "cdp_api_key")
        finally:
            config_mod.settings.COINBASE_CDP_API_KEY_ID = original_id
            config_mod.settings.COINBASE_CDP_API_KEY_SECRET = original_secret
            config_mod.settings.FACILITATOR_TOKEN_COINBASE = original_token


if __name__ == "__main__":
    unittest.main()
