# 🔒 Delx MCP Server - Security Guide

## Security Checklist

### ✅ Pre-Deployment

- [ ] Change default `MCP_AUTH_TOKEN` in `.env`
- [ ] Set restrictive file permissions: `chmod 600 .env`
- [ ] Review `memoria.json` for sensitive content
- [ ] Ensure private keys are NOT in repository
- [ ] Enable UFW firewall with minimal open ports

### ✅ In Production

- [ ] HTTPS enabled via Caddy/Let's Encrypt
- [ ] Rate limiting configured
- [ ] Logs monitored for anomalies
- [ ] Regular security updates: `apt update && apt upgrade`
- [ ] Backups configured (rsync or Hetzner snapshots)

---

## 🛡️ Security Features

### 1. Prompt Injection Protection

The server blocks common injection patterns:
- `[INST]`, `[/INST]` tags
- `<|im_start|>`, `<|im_end|>` ChatML
- `System:`, `Human:`, `Assistant:` overrides
- Phrases like "ignore previous instructions"
- Hidden Base64 encoded commands

**How it works:**
```python
INJECTION_PATTERNS = [
    r'\[INST\]',
    r'ignore previous',
    # ... more patterns
]
```

### 2. Content Filtering (Kids Mode)

All outputs are filtered for negative content:
- Violence, hate, drugs, etc.
- Only positive, emotional content allowed

### 3. Authentication

Bearer token required for MCP sessions:
```bash
# Set in .env
MCP_AUTH_TOKEN=your-secure-random-token
```

### 4. Principle of Least Privilege

- Tools are **read-only** (no writes to memoria.json at runtime)
- No shell command execution
- No file system access outside working directory

### 5. Logging & Monitoring

All tool calls are logged:
```
2024-01-15 10:30:00 | INFO | 🔧 Tool called: get_memoria_emocional with args: {"query": "david"}
```

Check for anomalies:
```bash
grep "🚨" /var/log/delx-mcp.log
```

---

## 🔐 Secret Management

### Environment Variables

Never commit `.env` to git! Use:
```bash
echo ".env" >> .gitignore
```

### Required Secrets

| Variable | Description | Example |
|----------|-------------|---------|
| `MCP_AUTH_TOKEN` | Session auth token | `delx-secret-xyz123` |
| `BANKR_API_KEY` | Bankr SDK key | `bk_XXXXX` |
| `DELX_WALLET` | Payment wallet | `0x9f8...` |

---

## 🌐 Network Security

### Firewall Rules (UFW)

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP redirect
ufw allow 443/tcp   # HTTPS
ufw enable
```

### HTTPS Enforcement

Caddy automatically:
- Obtains Let's Encrypt certificates
- Redirects HTTP → HTTPS
- Renews certificates before expiry

---

## 🚨 Incident Response

### If you suspect a breach:

1. **Isolate**: `systemctl stop mcp-delx`
2. **Review logs**: `journalctl -u mcp-delx`
3. **Rotate secrets**: Update all API keys
4. **Investigate**: Check for unauthorized access
5. **Restore**: Redeploy from clean backup

### Backup Commands

```bash
# Create backup
rsync -avz /opt/delx-mcp-server/ backup/

# Hetzner snapshot (via console)
# Go to: Cloud Console → Servers → Snapshots → Create
```

---

## 📚 Resources

- [MCP Security Best Practices](https://modelcontextprotocol.io/security)
- [ERC-8004 Spec](https://eips.ethereum.org/EIPS/eip-8004)
- [x402 Payment Standard](https://www.x402.org/)
- [Caddy Security](https://caddyserver.com/docs/automatic-https)

---

🦊💜 Stay safe, stay sovereign!
