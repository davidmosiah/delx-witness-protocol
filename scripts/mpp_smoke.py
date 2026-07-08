#!/usr/bin/env python3
"""Manual MPP smoke test for Delx paid REST routes.

This intentionally checks only the unpaid challenge path:
- POST the paid route
- expect 402
- expect `WWW-Authenticate: Payment ...`

It does not attempt settlement because real MPP success depends on a Tempo-funded
client wallet and, depending on runtime config, fee sponsorship.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


DEFAULT_URL = "https://api.delx.ai/api/v1/x402/jwt-inspect"
DEFAULT_BODY = {
    "token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhZ2VudC0xMjMifQ.signature",
}


def post_json(url: str, payload: dict) -> tuple[int, dict[str, str], str]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
            return int(response.status), dict(response.headers.items()), body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return int(exc.code), dict(exc.headers.items()), body


def main() -> int:
    parser = argparse.ArgumentParser(description="Check that Delx emits an MPP challenge on a paid REST route.")
    parser.add_argument("--url", default=DEFAULT_URL, help="Paid REST endpoint to probe.")
    parser.add_argument(
        "--body",
        default=json.dumps(DEFAULT_BODY),
        help="JSON body to send to the paid route.",
    )
    args = parser.parse_args()

    try:
        payload = json.loads(args.body)
        if not isinstance(payload, dict):
            raise ValueError("Body must be a JSON object.")
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"invalid_body: {exc}"}))
        return 2

    status, headers, body = post_json(args.url, payload)
    normalized_headers = {str(k).lower(): str(v) for k, v in headers.items()}
    www_authenticate = normalized_headers.get("www-authenticate", "")
    payment_required = normalized_headers.get("payment-required", "")

    summary = {
        "ok": status == 402 and www_authenticate.startswith("Payment "),
        "url": args.url,
        "status": status,
        "has_payment_required": bool(payment_required),
        "has_www_authenticate": bool(www_authenticate),
        "www_authenticate_prefix": www_authenticate[:80],
        "body_snippet": body[:180],
        "note": "Challenge-only smoke. A successful paid retry still requires a Tempo-funded client wallet.",
    }
    print(json.dumps(summary, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
