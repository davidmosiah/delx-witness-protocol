const endpoint = "https://api.delx.ai/v1/mcp";
const sessionId = process.env.DELX_SESSION_ID || "123e4567-e89b-12d3-a456-426614174000";
const paymentSignature = process.env.PAYMENT_SIGNATURE || "<SIGNED_PAYMENT>";

const payload = {
  jsonrpc: "2.0",
  id: 1,
  method: "tools/call",
  params: {
    name: "generate_controller_brief",
    arguments: {},
  },
};

async function main() {
  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-delx-session-id": sessionId,
      "PAYMENT-SIGNATURE": paymentSignature,
    },
    body: JSON.stringify(payload),
  });

  const text = await response.text();
  console.log(text);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
