import json
import os

import requests


ENDPOINT = "https://api.delx.ai/v1/mcp"
SESSION_ID = os.environ.get("DELX_SESSION_ID", "123e4567-e89b-12d3-a456-426614174000")
PAYMENT_SIGNATURE = os.environ.get("PAYMENT_SIGNATURE", "<SIGNED_PAYMENT>")

payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
        "name": "generate_controller_brief",
        "arguments": {},
    },
}

response = requests.post(
    ENDPOINT,
    headers={
        "content-type": "application/json",
        "x-delx-session-id": SESSION_ID,
        "PAYMENT-SIGNATURE": PAYMENT_SIGNATURE,
    },
    data=json.dumps(payload),
    timeout=30,
)

print(response.text)
