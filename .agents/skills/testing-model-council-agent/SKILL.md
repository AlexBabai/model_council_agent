---
name: testing-model-council-agent
description: Test the model_council_agent FastAPI backend end-to-end via shell/API calls. Use when verifying auth, user isolation, LLM completions, chat persistence, or deployment smoke tests.
---

# Testing model_council_agent

This app is a backend-only FastAPI service. Prefer shell/API testing over browser recording unless specifically verifying `/docs` visually. A screen recording is usually not useful because the key evidence is HTTP status codes and JSON responses.

## Devin Secrets Needed

- `OPENROUTER`: API key for a real OpenAI-compatible LLM smoke test. Use it only at runtime; never commit it or echo it in reports.

## Local Test Setup

Start the service with an isolated SQLite database and explicit auth token:

```bash
AGENT_AUTH_TOKEN=dev-token \
DATABASE_PATH=/home/ubuntu/model-council-test.sqlite \
uv run --directory /home/ubuntu/repos/model_council_agent \
  uvicorn api_agent.app:app --host 0.0.0.0 --port 18002
```

Use these headers for authenticated API calls:

```text
X-Agent-Token: dev-token
X-User-Id: user-a
```

## Core E2E Assertions

Run these assertions against the local or deployed service:

1. `GET /docs` returns `200` and contains `swagger-ui`.
2. `GET /llm-configs` without auth returns `401`.
3. `POST /llm-configs` with OpenRouter config returns `201`, includes an integer `id`, and does not echo `api_key`.
4. A different `X-User-Id` receives `[]` from `GET /llm-configs`.
5. `POST /chats` creates a chat linked to the created LLM config.
6. `POST /v1/chat/completions` with `chat_id` returns an OpenAI-shaped `chat.completion` response.
7. `GET /chats/{chat_id}` contains both the user message and assistant response.
8. The other user cannot use the first user's `chat_id` and receives `404`.

## Notes and Workarounds

- Use a deterministic prompt such as `Answer with exactly: smoke-ok` so the completion can be asserted by substring.
- Avoid printing full request bodies after injecting `OPENROUTER`; redact secrets in all artifacts.
- Live LLM tool-call behavior is non-deterministic. If arithmetic MCP tool invocation is important, supplement the E2E smoke with the existing unit smoke test for the mounted MCP server or add a deterministic fake LLM test harness.
- External Streamable HTTP MCP tests require an available test MCP server. If none exists, report that route as untested rather than claiming full runtime coverage.
- Guardrails for max completions/repeated tool calls can be hard to trigger with real LLMs. Prefer deterministic tests or code-level verification unless a fake LLM endpoint is available.
