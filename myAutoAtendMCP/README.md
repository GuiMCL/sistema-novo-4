# MCP Agendamentos — agente de WhatsApp + painel + servidor MCP

Serviço único (FastAPI) que concentra o produto: o **agente de IA do WhatsApp**
(pydantic-ai), as **ferramentas de agendamento**, o **painel `/admin`** e um
endpoint **MCP** opcional para clients externos. Persistência em **SQLite**,
autorização por telefone feita em código.

## O que faz

- Pipeline do WhatsApp: webhook da Evolution → mídia (áudio/imagem → texto) →
  debounce por contato → agente → resposta em bolhas com digitação simulada
- Agente pydantic-ai com 12 tools (3 níveis de permissão: aberto / dono ou
  próprio / dono) e memória de conversa por contato (janela de 50 mensagens)
- Provedores de IA configuráveis por uso (texto/áudio/imagem) pelo painel
- Servidor MCP (FastMCP, streamable-http) em `/mcp/` p/ clients externos
- Persistência **SQLite** (SQLModel) — sobrevive a reinício, em volume Docker
- Telefone normalizado para **E.164** com `phonenumbers`; fuso aplicado
- Painel web `/admin` (HTTP Basic): serviços, agenda, pareamento WhatsApp por
  QR, provedores de IA e instruções do agente

## Como rodar (local)

```bash
uv run --with-requirements requirements.txt uvicorn app.main:app --reload --port 8000
```

- Painel: http://localhost:8000/admin  (usuário/senha: `ADMIN_USER`/`ADMIN_PASS`)
- Webhook: http://localhost:8000/webhook/whatsapp/receberMensagem
- Endpoint MCP: http://localhost:8000/mcp/
- Health: http://localhost:8000/health

## Como rodar (Docker — já integrado no compose do projeto)

O serviço `mcp-agendamentos` está no `docker-compose.yml` da raiz. Sobe junto
com o resto:

```bash
docker compose up -d --build
```

Container: `mcp_agendamentos` na rede `evolution-net`. A Evolution entrega o
webhook em `http://mcp_agendamentos:8000`. Banco em volume `mcp_data`
(`/data/agendamentos.db`).

## Variáveis de ambiente

| Var | Default | Função |
|---|---|---|
| `MCP_DB_PATH` | `agendamentos.db` (`/data/...` no Docker) | caminho do SQLite |
| `OWNER_PHONE` | placeholder no compose | telefone do dono — configurado pelo painel (Configuração geral) |
| `MCP_TZ` | `America/Sao_Paulo` | fuso das conversões de data/hora |
| `ADMIN_USER` / `ADMIN_PASS` | `LOGIN` / `SENHA` do `.env` raiz | credenciais do painel |
| `EVOLUTION_API_URL/KEY/INSTANCE` | via compose | Evolution API (pipeline + painel) |
| `MCP_WEBHOOK_URL` | `http://mcp_agendamentos:8000/webhook/...` | URL deste serviço vista pela Evolution |

## Estrutura

```
app/
  main.py      # FastAPI: webhook + /admin + /mcp; startup cria instância Evolution
  whatsapp.py  # pipeline: webhook → mídia → debounce → agente → bolhas → envio
  agente.py    # agente pydantic-ai: tools, memória (SQLite), system prompt
  ia.py        # provedores de IA (SQLite): chaves, modelos, transcrição, visão
  evolution.py # cliente Evolution: painel (sync), pipeline (async), bootstrap
  tools.py     # 12 tools de agendamento (FastMCP) + validação de fuso/passado
  auth.py      # autorização (dono / próprio cliente) + contextvar do solicitante
  db.py        # SQLite via SQLModel
  phone.py     # normalização E.164 (phonenumbers)
  admin.py     # rotas do painel web
  config.py    # settings via env
  templates/admin.html
Dockerfile · requirements.txt
```

## Tools expostas

Abertas: `listar_servicos`, `consultar_horarios_disponiveis`, `agendar`,
`meus_agendamentos`.

Dono ou próprio cliente: `reagendar`, `cancelar`.

Apenas dono: `fechar_data`, `abrir_data`, `remanejar_dia`, `criar_servico`,
`editar_servico`, `ver_agenda_completa`.

## Modelo de segurança (importante)

Quem é o solicitante **não** é decidido pelo modelo. O pipeline do WhatsApp
grava o `remoteJid` do remetente do webhook no contextvar do solicitante antes
de rodar o agente (`app/whatsapp.py`); `auth.requester()` **sempre prefere**
esse valor ao argumento `telefone_solicitante` que aparece nas tools.
Resultado: o modelo não consegue se passar por outro número. A decisão de
autorização acontece em `app/auth.py`, em código.

O `remoteJid` vem com sufixo (ex.: `554599307290@s.whatsapp.net`); o
`@s.whatsapp.net` é descartado em `phone.normalizar()` antes de comparar.

Clients MCP externos (endpoint `/mcp/`) usam o mesmo esquema via query
`?solicitante=` ou header `X-Solicitante-Telefone` (middleware em `main.py`).

## Evolução futura (não essencial)

- Painel: trocar HTTP Basic por login de sessão com senha em hash (bcrypt) + cookie.
- Áudio via OpenRouter (endpoint próprio de transcrição, JSON base64).
- Migrar SQLite → Postgres se precisar de múltiplas instâncias do servidor
  (aí a unicidade de horário e o debounce em memória devem ir pro banco/Redis).
- Validações extras: antecedência mínima/máxima, duração mínima, horário comercial por dia da semana.
