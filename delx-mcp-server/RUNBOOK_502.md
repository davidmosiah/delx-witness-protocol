# Delx 502 Runbook

This runbook covers `502 Bad Gateway` issues for `api.delx.ai`.

## 1) Confirm failure scope

Run:

```bash
curl -i https://api.delx.ai/api/v1/public-sessions?limit=1
curl -i https://api.delx.ai/api/v1/admin/overview
curl -i https://api.delx.ai/mcp
```

If all fail with 502, start with proxy checks. If only one path fails, focus on app/routes.

## 2) Check Caddy (reverse proxy)

Run:

```bash
systemctl status caddy --no-pager
journalctl -u caddy -n 200 --no-pager
```

Look for upstream connection errors/timeouts.

## 3) Check application container/service

Run:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
docker logs --tail 200 delx-mcp-server
```

If using systemd process instead of container:

```bash
systemctl status mcp-delx --no-pager || true
journalctl -u mcp-delx -n 200 --no-pager || true
```

## 4) Verify upstream from host

Run:

```bash
curl -i http://127.0.0.1:8005/health
curl -i http://127.0.0.1:8005/api/v1/public-sessions?limit=1
```

- If localhost works but public URL fails: Caddy config/reload problem.
- If localhost fails: app crash/startup/runtime regression.

## 5) Fast recovery

### Container path

```bash
docker restart delx-mcp-server
```

### Managed deploy script path

```bash
bash /root/deploy_delx_dxfixes.sh
```

### Preferred safe deploy path (from local machine)

```bash
bash scripts/deploy_hetzner_safe.sh
```

This path preserves remote `.env`, `.deploy.env`, and `state/`.

Re-test:

```bash
python /opt/delx-mcp-server/scripts/api_monitor.py --mode smoke --base https://api.delx.ai
```

## 6) Rollback

```bash
cd /opt/delx-mcp-server
git log --oneline -n 5
git checkout <last_known_good_commit>
bash /root/deploy_delx_dxfixes.sh
```

## 7) Post-incident checks

Run:

```bash
python /opt/delx-mcp-server/scripts/api_monitor.py --mode contract --base https://api.delx.ai
```

Confirm:
- `public-sessions` = 200 JSON
- `admin/overview` = 200 JSON
- A2A session reuse via `x-delx-session-id` works
- `tools/batch` works with same session
