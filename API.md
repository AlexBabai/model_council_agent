# Model Council Agent API

## Stack

- FastAPI + Uvicorn for async HTTP and `/docs` OpenAPI UI.
- Pydantic v2 for request/response schemas.
- aiosqlite for persistent per-user chat/config storage.
- OpenAI Python SDK for OpenAI-compatible `POST /v1/chat/completions`.
- FastMCP + MCP SDK for built-in and external Streamable HTTP MCP tools.
- uv, Ruff, Pyrefly, Pytest for packaging, linting, type checking, and tests.
- Docker / Docker Compose for deployment.

## Auth

Every API endpoint, except `/health`, `/docs`, `/openapi.json`, and the mounted arithmetic MCP
transport, expects:

```text
X-Agent-Token: <AGENT_AUTH_TOKEN>
X-User-Id: <any-user-id>
```

The token is checked against the `AGENT_AUTH_TOKEN` environment variable. All database reads and
writes are scoped by `X-User-Id`.

## Run locally

```bash
cp .env.example .env
uv sync
AGENT_AUTH_TOKEN=dev-token DATABASE_PATH=db.sqlite uv run uvicorn api_agent.app:app --reload
```

OpenAPI docs: <http://127.0.0.1:8000/docs>

## Run with Docker

```bash
AGENT_AUTH_TOKEN=dev-token PORT=8000 docker compose up --build
```

## Typical flow

```bash
BASE=http://127.0.0.1:8000
AUTH=(-H 'X-Agent-Token: dev-token' -H 'X-User-Id: alice')

curl -s "$BASE/llm-configs" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"name":"OpenRouter","base_url":"https://openrouter.ai/api","api_key":"...","model":"openai/gpt-4o-mini"}'

curl -s "$BASE/chats" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"title":"Homework","llm_config_ids":[1]}'

curl -s "$BASE/chats/1/messages" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"content":"What is 21 * 2? Use tools if useful."}'

curl -s "$BASE/chats/1" "${AUTH[@]}"
```

## OpenAI-compatible endpoint

```bash
curl -s "$BASE/v1/chat/completions" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d '{
    "chat_id": 1,
    "messages": [{"role":"user","content":"Divide 10 by 2"}],
    "stream": false
  }'
```

## MCP

- Built-in arithmetic MCP is mounted at `/mcp/arithmetic/mcp`.
- Built-in tools are always available to chats:
  - `arithmetic__double`
  - `arithmetic__divide`
- User MCP configs are Streamable HTTP only:

```json
{
  "name": "my-tools",
  "url": "https://example.com/mcp",
  "token": "bearer-token"
}
```

Remote MCP calls send `Authorization: Bearer <token>`.

## Guardrails

- Agent loop stops with an error after more than 10 LLM completions.
- Agent loop stops if the same tool is requested more than 2 times in a row.
- MCP attachments can only be changed between agent iterations; active chats return `409`.
