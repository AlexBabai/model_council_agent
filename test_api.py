import os
import tempfile

from fastapi.testclient import TestClient

os.environ["AGENT_AUTH_TOKEN"] = "test-token"
os.environ["DATABASE_PATH"] = f"{tempfile.mkdtemp()}/test.sqlite"

from api_agent.app import app  # noqa: E402


def test_auth_is_required() -> None:
    with TestClient(app) as client:
        response = client.get("/llm-configs")

    assert response.status_code == 401


def test_user_data_isolated() -> None:
    headers_a = {"X-Agent-Token": "test-token", "X-User-Id": "user-a"}
    headers_b = {"X-Agent-Token": "test-token", "X-User-Id": "user-b"}

    with TestClient(app) as client:
        created = client.post(
            "/llm-configs",
            headers=headers_a,
            json={
                "name": "OpenRouter",
                "base_url": "https://openrouter.ai/api",
                "api_key": "secret",
                "model": "openai/gpt-4o-mini",
            },
        )
        response_a = client.get("/llm-configs", headers=headers_a)
        response_b = client.get("/llm-configs", headers=headers_b)

    assert created.status_code == 201
    assert len(response_a.json()) == 1
    assert response_b.json() == []


def test_openapi_docs_are_available() -> None:
    with TestClient(app) as client:
        response = client.get("/docs")

    assert response.status_code == 200
