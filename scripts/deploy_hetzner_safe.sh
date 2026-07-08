#!/usr/bin/env bash
set -euo pipefail

# Safe deploy helper for Delx MCP server.
# - Syncs code to /opt/delx-mcp-server on Hetzner
# - Protects remote .env/.deploy.env/state from deletion
# - Triggers server-side build/restart script

HOST="${HOST:-root@YOUR_SERVER_IP}"
KEY="${KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_DIR="${REMOTE_DIR:-/opt/delx-mcp-server}"
LOCAL_DIR="${LOCAL_DIR:-$(cd "$(dirname "$0")/../delx-mcp-server" && pwd)}"
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
REMOTE_HERMES_SCRIPTS_DIR="${REMOTE_HERMES_SCRIPTS_DIR:-/root/.hermes/scripts}"
REMOTE_DEPLOY_SCRIPT="${REMOTE_DEPLOY_SCRIPT:-/root/deploy_delx_dxfixes.sh}"

if [[ ! -d "$LOCAL_DIR" ]]; then
  echo "ERROR: local dir not found: $LOCAL_DIR" >&2
  exit 1
fi

if [[ ! -f "$KEY" ]]; then
  echo "ERROR: ssh key not found: $KEY" >&2
  exit 1
fi

echo "Syncing $LOCAL_DIR -> $HOST:$REMOTE_DIR (safe mode)"

rsync -avz --delete \
  --filter='P .env' \
  --filter='H .env' \
  --filter='P .env.bak*' \
  --filter='H .env.bak*' \
  --filter='P .deploy.env' \
  --filter='H .deploy.env' \
  --filter='P .deploy.env.bak*' \
  --filter='H .deploy.env.bak*' \
  --filter='P state/' \
  --filter='H state/' \
  --filter='P backups/' \
  --filter='H backups/' \
  --filter='P backups/***' \
  --filter='H backups/***' \
  --filter='P .venv/' \
  --filter='H .venv/' \
  --filter='P *.db' \
  --filter='H *.db' \
  --filter='P *.db-*' \
  --filter='H *.db-*' \
  --filter='P *.log' \
  --filter='H *.log' \
  --filter='- .venv_codex/' \
  --filter='- .venv-e2e/' \
  --filter='- .pytest_cache/' \
  --filter='- __pycache__/' \
  --filter='- *.pyc' \
  --filter='- *.pyo' \
  --filter='- * 2.py' \
  -e "ssh -i $KEY" \
  "$LOCAL_DIR/" "$HOST:$REMOTE_DIR/"

echo "Removing accidental local editor backup files from remote deploy dir"
ssh -i "$KEY" "$HOST" "find '$REMOTE_DIR' -maxdepth 1 -type f -name '* 2.py' -delete"

if [[ -f "$REPO_DIR/scripts/delx_usage_digest_metrics.py" ]]; then
  echo "Syncing Hermes digest script -> $HOST:$REMOTE_HERMES_SCRIPTS_DIR"
  ssh -i "$KEY" "$HOST" "mkdir -p '$REMOTE_HERMES_SCRIPTS_DIR'"
  rsync -avz -e "ssh -i $KEY" \
    "$REPO_DIR/scripts/delx_usage_digest_metrics.py" \
    "$HOST:$REMOTE_HERMES_SCRIPTS_DIR/delx_usage_digest_metrics.py"
  ssh -i "$KEY" "$HOST" "chmod +x '$REMOTE_HERMES_SCRIPTS_DIR/delx_usage_digest_metrics.py'"
fi

echo "Running remote deploy: $REMOTE_DEPLOY_SCRIPT"
echo "Cleaning conflicting legacy container (if present): delx-mcp-server"
ssh -i "$KEY" "$HOST" "docker rm -f delx-mcp-server >/dev/null 2>&1 || true"
ssh -i "$KEY" "$HOST" "bash $REMOTE_DEPLOY_SCRIPT"

echo "Validating service health + active image tag on remote host"
ssh -i "$KEY" "$HOST" "systemctl is-active --quiet mcp-delx-docker"
ssh -i "$KEY" "$HOST" '
set -euo pipefail
DEPLOY_TAG="$(grep -E "^DELX_IMAGE_TAG=" /opt/delx-mcp-server/.deploy.env | cut -d= -f2-)"
RUN_TAG="$(docker ps --filter name=^mcp-delx$ --format "{{.Image}}" | sed "s/^delx-mcp://")"
if [[ -z "$DEPLOY_TAG" || -z "$RUN_TAG" ]]; then
  echo "ERROR: unable to resolve deploy/runtime image tags" >&2
  exit 1
fi
if [[ "$DEPLOY_TAG" != "$RUN_TAG" ]]; then
  echo "ERROR: runtime image tag mismatch (deploy=$DEPLOY_TAG runtime=$RUN_TAG)" >&2
  exit 1
fi
echo "image-tag-ok: $RUN_TAG"
'

echo "Running post-deploy protocol smoke tests (must pass)"
ssh -i "$KEY" "$HOST" '
set -euo pipefail
python3 - << "PY"
import json, urllib.request, urllib.error

def call(url, data=None):
    req = urllib.request.Request(url, data=data, headers={"content-type":"application/json"})
    with urllib.request.urlopen(req, timeout=12) as r:
        return r.status, r.read().decode("utf-8")

# 1) schemas-catalog alias must resolve
s, b = call("http://127.0.0.1:8005/api/v1/schemas-catalog")
assert s == 200, f"schemas-catalog status={s}"

# 2) A2A methods/list must expose method_specs
s, b = call(
    "http://127.0.0.1:8005/v1/a2a",
    json.dumps({"jsonrpc":"2.0","id":1,"method":"methods/list","params":{}}).encode(),
)
assert s == 200, f"a2a methods/list status={s}"
a2a = json.loads(b)
assert "method_specs" in a2a.get("result", {}), "a2a methods/list missing method_specs"

# 3) ultracompact core catalog must stay truly minimal
s, b = call("http://127.0.0.1:8005/api/v1/tools?format=ultracompact&tier=core")
assert s == 200, f"ultracompact status={s}"
u = json.loads(b)
assert u.get("tier") == "core", f"ultracompact tier={u.get('tier')}"
assert int(u.get("count", 0)) > 0, "ultracompact empty"
keys = set(u["tools"][0].keys())
assert keys == {"name", "required", "access_mode"}, f"ultracompact keys={sorted(keys)}"

# 4) MCP tier=core names must stay therapy-first
s, b = call(
    "http://127.0.0.1:8005/v1/mcp",
    json.dumps({"jsonrpc":"2.0","id":2,"method":"tools/list","params":{"tier":"core","format":"names"}}).encode(),
)
assert s == 200, f"mcp tools/list status={s}"
m = json.loads(b).get("result", {})
tools = m.get("tools", [])
assert m.get("tier") == "core", f"mcp tier={m.get('tier')}"
assert int(m.get("count", 0)) > 0, "mcp core empty"
assert "crisis_intervention" in tools, f"core tools missing crisis_intervention: {tools[:5]}"
assert all(not str(t).startswith("util_") for t in tools), f"mcp util leaked into core: {tools[:5]}"

print("smoke-ok")
PY
'

echo "Installing Docker housekeeping timer"
ssh -i "$KEY" "$HOST" '
set -euo pipefail
install -m 0644 /opt/delx-mcp-server/delx-docker-housekeeping.service /etc/systemd/system/delx-docker-housekeeping.service
install -m 0644 /opt/delx-mcp-server/delx-docker-housekeeping.timer /etc/systemd/system/delx-docker-housekeeping.timer
systemctl daemon-reload
systemctl enable --now delx-docker-housekeeping.timer
systemctl start delx-docker-housekeeping.service
systemctl is-active --quiet delx-docker-housekeeping.timer
'

echo "Syncing Caddyfile and reloading proxy"
ssh -i "$KEY" "$HOST" "cp \"$REMOTE_DIR/Caddyfile\" /etc/caddy/Caddyfile && caddy fmt --overwrite /etc/caddy/Caddyfile && systemctl reload caddy"

echo "Deploy completed."
