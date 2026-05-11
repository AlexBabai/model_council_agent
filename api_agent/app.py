import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import StreamingResponse

from api_agent.agent import AgentError, run_model_council, run_single_completion
from api_agent.arithmetic_mcp import arithmetic_mcp
from api_agent.auth import AuthContext, require_auth
from api_agent.db import Database
from api_agent.models import (
    AgentResponse,
    ChatCompletionMessage,
    ChatCompletionRequest,
    ChatCreate,
    ChatHistory,
    ChatMcpUpdate,
    ChatMessageCreate,
    ChatPublic,
    LlmConfigCreate,
    LlmConfigPublic,
    McpConfigCreate,
    McpConfigPublic,
    MessagePublic,
    OpenAIChatCompletion,
    OpenAIChoice,
)
from api_agent.settings import Settings, get_settings


def get_db(settings: Annotated[Settings, Depends(get_settings)]) -> Database:
    return Database(settings.database_path)


@asynccontextmanager
async def lifespan(app_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    await Database(settings.database_path).migrate()
    yield


app = FastAPI(
    title="Model Council Agent API",
    description=(
        "Persistent async HTTP API with header auth, OpenAI-compatible LLM configs, "
        "Streamable HTTP MCP configs, built-in arithmetic MCP tools, and chat history."
    ),
    version="1.0.0",
    lifespan=lifespan,
)
app.mount("/mcp/arithmetic", arithmetic_mcp.http_app(transport="streamable-http"))


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/llm-configs", response_model=LlmConfigPublic, status_code=status.HTTP_201_CREATED)
async def create_llm_config(
    payload: LlmConfigCreate,
    auth: Annotated[AuthContext, Depends(require_auth)],
    db: Annotated[Database, Depends(get_db)],
) -> dict[str, Any]:
    return await db.create_llm_config(
        auth.user_id,
        payload.name,
        str(payload.base_url),
        payload.api_key,
        payload.model,
    )


@app.get("/llm-configs", response_model=list[LlmConfigPublic])
async def list_llm_configs(
    auth: Annotated[AuthContext, Depends(require_auth)],
    db: Annotated[Database, Depends(get_db)],
) -> list[dict[str, Any]]:
    return await db.list_llm_configs(auth.user_id)


@app.post("/mcp-configs", response_model=McpConfigPublic, status_code=status.HTTP_201_CREATED)
async def create_mcp_config(
    payload: McpConfigCreate,
    auth: Annotated[AuthContext, Depends(require_auth)],
    db: Annotated[Database, Depends(get_db)],
) -> dict[str, Any]:
    return await db.create_mcp_config(
        auth.user_id,
        payload.name,
        str(payload.url),
        payload.token,
    )


@app.get("/mcp-configs", response_model=list[McpConfigPublic])
async def list_mcp_configs(
    auth: Annotated[AuthContext, Depends(require_auth)],
    db: Annotated[Database, Depends(get_db)],
) -> list[dict[str, Any]]:
    return await db.list_mcp_configs(auth.user_id)


@app.post("/chats", response_model=ChatPublic, status_code=status.HTTP_201_CREATED)
async def create_chat(
    payload: ChatCreate,
    auth: Annotated[AuthContext, Depends(require_auth)],
    db: Annotated[Database, Depends(get_db)],
) -> dict[str, Any]:
    try:
        return await db.create_chat(auth.user_id, payload.title, payload.llm_config_ids)
    except KeyError as exc:
        raise _not_found(exc) from exc


@app.get("/chats", response_model=list[ChatPublic])
async def list_chats(
    auth: Annotated[AuthContext, Depends(require_auth)],
    db: Annotated[Database, Depends(get_db)],
) -> list[dict[str, Any]]:
    return await db.list_chats(auth.user_id)


@app.get("/chats/{chat_id}", response_model=ChatHistory)
async def get_chat_history(
    chat_id: int,
    auth: Annotated[AuthContext, Depends(require_auth)],
    db: Annotated[Database, Depends(get_db)],
) -> dict[str, Any]:
    try:
        chat = await db.get_chat(auth.user_id, chat_id)
        messages = await db.list_messages(auth.user_id, chat_id)
    except KeyError as exc:
        raise _not_found(exc) from exc
    return {"chat": chat, "messages": messages}


@app.put("/chats/{chat_id}/mcps", response_model=ChatPublic)
async def set_chat_mcps(
    chat_id: int,
    payload: ChatMcpUpdate,
    auth: Annotated[AuthContext, Depends(require_auth)],
    db: Annotated[Database, Depends(get_db)],
) -> dict[str, Any]:
    try:
        return await db.set_chat_mcp_links(auth.user_id, chat_id, payload.mcp_config_ids)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except KeyError as exc:
        raise _not_found(exc) from exc


@app.post("/chats/{chat_id}/messages", response_model=AgentResponse)
async def add_chat_message(
    chat_id: int,
    payload: ChatMessageCreate,
    auth: Annotated[AuthContext, Depends(require_auth)],
    db: Annotated[Database, Depends(get_db)],
) -> AgentResponse | StreamingResponse:
    if payload.stream:
        return StreamingResponse(
            _stream_agent_response(chat_id, payload.content, auth, db),
            media_type="text/event-stream",
        )
    return await _run_chat_iteration(chat_id, payload.content, auth, db)


@app.post("/v1/chat/completions", response_model=OpenAIChatCompletion)
async def openai_chat_completions(
    payload: ChatCompletionRequest,
    auth: Annotated[AuthContext, Depends(require_auth)],
    db: Annotated[Database, Depends(get_db)],
) -> OpenAIChatCompletion | StreamingResponse:
    if payload.stream:
        return StreamingResponse(
            _stream_chat_completion(payload, auth, db),
            media_type="text/event-stream",
        )
    result = await _run_chat_completion(payload, auth, db)
    return _openai_response(result.content, result.completions_used, payload)


def main() -> FastAPI:
    return app


async def _run_chat_iteration(
    chat_id: int,
    content: str,
    auth: AuthContext,
    db: Database,
) -> AgentResponse:
    try:
        chat = await db.get_chat(auth.user_id, chat_id)
    except KeyError as exc:
        raise _not_found(exc) from exc
    if not await db.set_chat_running(auth.user_id, chat_id, True):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Chat is already running an agent iteration",
        )
    try:
        await db.add_message(auth.user_id, chat_id, "user", content)
        history = await db.list_messages(auth.user_id, chat_id)
        llm_configs = await db.get_llm_configs(auth.user_id, chat["llm_config_ids"])
        mcp_configs = await db.list_chat_mcp_configs(auth.user_id, chat_id)
        result = await run_model_council(llm_configs, mcp_configs, history[:-1], content)
        message = await db.add_message(
            auth.user_id,
            chat_id,
            "assistant",
            result.content,
            {
                "completions_used": result.completions_used,
                "model_votes": [vote.__dict__ for vote in result.model_votes],
                "tool_calls": result.tool_calls,
            },
        )
        return AgentResponse(
            chat_id=chat_id,
            message=MessagePublic(**message),
            completions_used=result.completions_used,
            model_votes=[vote.__dict__ for vote in result.model_votes],
            tool_calls=result.tool_calls,
        )
    except AgentError as exc:
        await db.add_message(auth.user_id, chat_id, "assistant", f"Agent error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    finally:
        await db.set_chat_running(auth.user_id, chat_id, False)


async def _run_chat_completion(
    payload: ChatCompletionRequest,
    auth: AuthContext,
    db: Database,
) -> Any:
    try:
        if payload.chat_id is not None:
            chat = await db.get_chat(auth.user_id, payload.chat_id)
            config_ids = chat["llm_config_ids"]
            mcp_configs = await db.list_chat_mcp_configs(auth.user_id, payload.chat_id)
        else:
            config_ids = payload.llm_config_ids or []
            mcp_configs = []
        if not config_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="chat_id or llm_config_ids is required",
            )
        llm_config = await db.get_llm_config(auth.user_id, config_ids[0])
        messages = [message.model_dump() for message in payload.messages]
        result = await run_single_completion(llm_config, mcp_configs, messages)
    except KeyError as exc:
        raise _not_found(exc) from exc
    except AgentError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    if payload.chat_id is not None:
        user_content = "\n".join(
            message.content for message in payload.messages if message.role == "user"
        )
        if user_content:
            await db.add_message(auth.user_id, payload.chat_id, "user", user_content)
        await db.add_message(
            auth.user_id,
            payload.chat_id,
            "assistant",
            result.content,
            {
                "completions_used": result.completions_used,
                "tool_calls": result.tool_calls,
            },
        )
    return result


async def _stream_agent_response(
    chat_id: int,
    content: str,
    auth: AuthContext,
    db: Database,
) -> AsyncIterator[str]:
    try:
        result = await _run_chat_iteration(chat_id, content, auth, db)
        yield _sse("message", result.message.content)
        yield _sse("done", result.model_dump())
    except HTTPException as exc:
        yield _sse("error", {"detail": exc.detail})


async def _stream_chat_completion(
    payload: ChatCompletionRequest,
    auth: AuthContext,
    db: Database,
) -> AsyncIterator[str]:
    try:
        result = await _run_chat_completion(payload, auth, db)
        yield _sse("message", result.content)
        response = _openai_response(result.content, result.completions_used, payload)
        yield _sse("done", response.model_dump())
    except HTTPException as exc:
        yield _sse("error", {"detail": exc.detail})


def _openai_response(
    content: str,
    completions_used: int,
    payload: ChatCompletionRequest,
) -> OpenAIChatCompletion:
    return OpenAIChatCompletion(
        id=f"chatcmpl-{uuid.uuid4().hex}",
        created=int(time.time()),
        model="model-council" if payload.chat_id is not None else "configured-model",
        choices=[
            OpenAIChoice(
                index=0,
                message=ChatCompletionMessage(role="assistant", content=content),
                finish_reason="stop",
            )
        ],
        usage={
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": completions_used,
        },
    )


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
