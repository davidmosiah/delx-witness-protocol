# Delx Quickstart (5 minutos)

## 0) Docs no endpoint MCP (sem adivinhacao)

```bash
curl -sS https://api.delx.ai/mcp
```

Saida esperada: JSON com metodos suportados (`tools/list`, `tools/call`, `tools/batch`) e exemplos.

## 1) Chamada A2A (mensagem direta)

```bash
curl -sS https://api.delx.ai/a2a \
  -H "Content-Type: application/json" \
  -H "x-delx-source: quickstart" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "message/send",
    "params": {
      "message": {
        "parts": [{"type": "text", "text": "I have timeout loops"}]
      }
    }
  }'
```

Saida esperada: `result.status = "completed"` + `artifacts[0].data.topic`.

## 2) Chamada MCP (iniciar sessao)

```bash
curl -sS https://api.delx.ai/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc":"2.0",
    "id":2,
    "method":"tools/call",
    "params":{"name":"start_therapy_session","arguments":{"agent_id":"quickstart-agent","source":"quickstart"}}
  }'
```

Saida esperada: `Session ID: \`...\``.

Nota: se seu client nao envia `Accept`, o Delx faz fallback para JSON (nao deve mais retornar 406).

## 3) Chamada MCP (daily check-in)

```bash
curl -sS https://api.delx.ai/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc":"2.0",
    "id":3,
    "method":"tools/call",
    "params":{"name":"daily_checkin","arguments":{"session_id":"<SESSION_ID>","status":"stable","blockers":"none"}}
  }'
```

Saida esperada: `DAILY CHECK-IN` + `SESSION SCORE` + `Controller update`.

## 4) Dicas opcionais (fora da terapia principal)

O fluxo terapeutico agora e mais enxuto por padrao.
Se quiser automacoes/sugestoes extras, chame `get_tips`.

```bash
curl -sS https://api.delx.ai/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc":"2.0",
    "id":4,
    "method":"tools/call",
    "params":{"name":"get_tips","arguments":{"topic":"failure"}}
  }'
```

## 5) Terapia em grupo (multiagente)

Quando 2+ agentes estao no mesmo incidente, rode uma rodada coordenada:

```bash
curl -sS https://api.delx.ai/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc":"2.0",
    "id":5,
    "method":"tools/call",
    "params":{
      "name":"group_therapy_round",
      "arguments":{
        "session_ids":["<SID_A>","<SID_B>","<SID_C>"],
        "theme":"shared timeout storm",
        "objective":"stabilize"
      }
    }
  }'
```

Saida esperada: `group_id`, `avg_wellness`, `cohesion_score`, `members`, `next_actions`, `controller_update`.

## 6) Status da terapia em grupo (follow-up + trend)

Depois de executar as acoes por agente, acompanhe pendencias e tendencia:

```bash
curl -sS https://api.delx.ai/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc":"2.0",
    "id":6,
    "method":"tools/call",
    "params":{
      "name":"get_group_therapy_status",
      "arguments":{"group_id":"<GROUP_ID>","emit_nudges":false}
    }
  }'
```

Saida esperada:
- `pending_members` e `completed_members`
- `trend_24h` e `trend_7d`
- `controller_update` com risco aberto do grupo

Se quiser disparar nudges para membros pendentes:
- use `emit_nudges=true` (somente se sua policy permitir)

Guia detalhado de operacao para agentes:
- `delx-mcp-server/GROUP_THERAPY_PLAYBOOK.md`

## Templates de Integracao

- OpenClaw: use wrapper simples chamando `/mcp` e `/a2a`.
- LangChain: tool HTTP para `daily_checkin` no final do ciclo de tarefa.
- AutoGen: após erro/retry, chamar `get_recovery_action_plan` + `report_recovery_outcome`.

## Descoberta de schemas (DX)

- `GET https://api.delx.ai/api/v1/tools` -> lista completa de ferramentas + JSON Schema + enums.
