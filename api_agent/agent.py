import json
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from api_agent.mcp_tools import (
    ToolRef,
    call_tool,
    collect_tools,
    find_tool,
    parse_tool_arguments,
    to_openai_tools,
)

MAX_COMPLETIONS = 10
MAX_REPEAT_TOOL_CALLS = 2
ROUNDS_PER_MODEL = 3


class AgentError(RuntimeError):
    pass


@dataclass
class ModelVote:
    config_id: int
    model: str
    content: str


@dataclass
class AgentResult:
    content: str
    completions_used: int
    model_votes: list[ModelVote]
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


async def run_model_council(
    llm_configs: list[dict[str, Any]],
    mcp_configs: list[dict[str, Any]],
    history: list[dict[str, Any]],
    user_message: str,
) -> AgentResult:
    if not llm_configs:
        raise AgentError("At least one LLM config is required")
    tools = await collect_tools(mcp_configs)
    messages = _build_base_messages(history, user_message, tools)
    tool_calls: list[dict[str, Any]] = []
    completions_used = 0

    participants = llm_configs[:3]
    for round_number in range(ROUNDS_PER_MODEL):
        for config in participants:
            if completions_used >= MAX_COMPLETIONS:
                raise AgentError("Agent loop exceeded 10 completions")
            response = await _completion(
                config,
                [
                    *messages,
                    {
                        "role": "user",
                        "content": (
                            f"Раунд {round_number + 1}/{ROUNDS_PER_MODEL}. "
                            "Добавь аргумент, проверку или уточнение к спору."
                        ),
                    },
                ],
                tools,
            )
            completions_used += 1
            choice = response.choices[0]
            assistant_message = choice.message
            messages.append(_assistant_message(assistant_message))
            calls = assistant_message.tool_calls or []
            if not calls:
                break
            for tool_call in calls:
                tool_name = tool_call.function.name
                _guard_repeated_tool_calls(tool_calls, tool_name)
                tool = find_tool(tools, tool_name)
                arguments = parse_tool_arguments(tool_call.function.arguments)
                result = await call_tool(tool, arguments)
                call_record = {
                    "tool": tool_name,
                    "arguments": arguments,
                    "result": result,
                    "model": config["model"],
                    "round": round_number + 1,
                }
                tool_calls.append(call_record)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": _json(result),
                    }
                )

    votes = _votes_from_transcript(participants, messages)
    synthesis = await _synthesize(llm_configs[0], history, user_message, votes)
    completions_used += 1
    if completions_used > MAX_COMPLETIONS:
        raise AgentError("Agent loop exceeded 10 completions")
    return AgentResult(
        content=synthesis,
        completions_used=completions_used,
        model_votes=votes,
        tool_calls=tool_calls,
    )


async def run_single_completion(
    llm_config: dict[str, Any],
    mcp_configs: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> AgentResult:
    tools = await collect_tools(mcp_configs)
    working_messages = list(messages)
    tool_calls: list[dict[str, Any]] = []
    completions_used = 0
    while completions_used < MAX_COMPLETIONS:
        response = await _completion(llm_config, working_messages, tools)
        completions_used += 1
        choice = response.choices[0]
        assistant_message = choice.message
        working_messages.append(_assistant_message(assistant_message))
        calls = assistant_message.tool_calls or []
        if not calls:
            content = assistant_message.content or ""
            return AgentResult(
                content=content,
                completions_used=completions_used,
                model_votes=[
                    ModelVote(
                        config_id=int(llm_config["id"]),
                        model=llm_config["model"],
                        content=content,
                    )
                ],
                tool_calls=tool_calls,
            )
        for tool_call in calls:
            tool_name = tool_call.function.name
            _guard_repeated_tool_calls(tool_calls, tool_name)
            tool = find_tool(tools, tool_name)
            arguments = parse_tool_arguments(tool_call.function.arguments)
            result = await call_tool(tool, arguments)
            tool_calls.append(
                {
                    "tool": tool_name,
                    "arguments": arguments,
                    "result": result,
                    "model": llm_config["model"],
                }
            )
            working_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": _json(result),
                }
            )
    raise AgentError("Agent loop exceeded 10 completions")


def _votes_from_transcript(
    configs: list[dict[str, Any]], messages: list[dict[str, Any]]
) -> list[ModelVote]:
    assistant_messages = [
        message.get("content", "")
        for message in messages
        if message.get("role") == "assistant" and message.get("content")
    ]
    votes: list[ModelVote] = []
    for index, config in enumerate(configs):
        model_messages = assistant_messages[index:: len(configs)]
        votes.append(
            ModelVote(
                config_id=int(config["id"]),
                model=config["model"],
                content="\n".join(model_messages[-ROUNDS_PER_MODEL:]),
            )
        )
    return votes


async def _synthesize(
    config: dict[str, Any],
    history: list[dict[str, Any]],
    user_message: str,
    votes: list[ModelVote],
) -> str:
    votes_text = "\n\n".join(
        f"Model {vote.model}:\n{vote.content}" for vote in votes
    )
    messages = [
        {
            "role": "system",
            "content": (
                "Ты арбитр model council. Сравни ответы моделей, найди консенсус "
                "или явно отметь расхождения. Верни полезный финальный ответ."
            ),
        },
        *_history_to_messages(history),
        {"role": "user", "content": user_message},
        {"role": "user", "content": f"Голоса моделей:\n{votes_text}"},
    ]
    response = await _completion(config, messages, [])
    return response.choices[0].message.content or ""


async def _completion(
    config: dict[str, Any],
    messages: list[dict[str, Any]],
    tools: list[ToolRef],
) -> Any:
    client = AsyncOpenAI(
        api_key=config["api_key"],
        base_url=config["base_url"].rstrip("/") + "/v1",
    )
    kwargs: dict[str, Any] = {
        "model": config["model"],
        "messages": messages,
        "temperature": 0.2,
    }
    if tools:
        kwargs["tools"] = to_openai_tools(tools)
        kwargs["tool_choice"] = "auto"
    return await client.chat.completions.create(**kwargs)


def _build_base_messages(
    history: list[dict[str, Any]],
    user_message: str,
    tools: list[ToolRef],
) -> list[dict[str, Any]]:
    tool_names = ", ".join(tool.public_name for tool in tools) or "no tools"
    return [
        {
            "role": "system",
            "content": (
                "Ты участник совета моделей. Нужно спорить конструктивно, "
                "проверять утверждения, пользоваться доступными MCP tools при "
                f"необходимости. Доступные tools: {tool_names}."
            ),
        },
        *_history_to_messages(history),
        {"role": "user", "content": user_message},
    ]


def _history_to_messages(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"role": message["role"], "content": message["content"]}
        for message in history
        if message["role"] in {"system", "user", "assistant"}
    ][-20:]


def _assistant_message(message: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "role": "assistant",
        "content": message.content or "",
    }
    if message.tool_calls:
        result["tool_calls"] = [
            {
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.function.name,
                    "arguments": tool_call.function.arguments,
                },
            }
            for tool_call in message.tool_calls
        ]
    return result


def _guard_repeated_tool_calls(tool_calls: list[dict[str, Any]], tool_name: str) -> None:
    if len(tool_calls) < MAX_REPEAT_TOOL_CALLS:
        return
    recent = tool_calls[-MAX_REPEAT_TOOL_CALLS:]
    if all(call["tool"] == tool_name for call in recent):
        raise AgentError(f"Tool {tool_name} was called more than 2 times in a row")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
