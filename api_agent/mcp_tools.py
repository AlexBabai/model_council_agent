import json
from dataclasses import dataclass
from typing import Any

from fastmcp import Client
from fastmcp.client.auth import BearerAuth

from api_agent.arithmetic_mcp import arithmetic_mcp
from api_agent.models import ToolSpec


@dataclass(frozen=True)
class ToolRef:
    public_name: str
    server_name: str
    tool_name: str
    description: str | None
    parameters: dict[str, Any]
    url: str | None = None
    token: str | None = None


async def collect_tools(mcp_configs: list[dict[str, Any]]) -> list[ToolRef]:
    tools = await _collect_builtin_tools()
    for config in mcp_configs:
        tools.extend(await _collect_remote_tools(config))
    return tools


async def call_tool(tool: ToolRef, arguments: dict[str, Any]) -> Any:
    if tool.server_name == "arithmetic":
        async with Client(arithmetic_mcp) as client:
            result = await client.call_tool(tool.tool_name, arguments)
            return result.data
    auth = BearerAuth(tool.token) if tool.token else None
    async with Client(tool.url or "", auth=auth) as client:
        result = await client.call_tool(tool.tool_name, arguments)
        return result.data


def to_openai_tools(tools: list[ToolRef]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.public_name,
                "description": tool.description or "",
                "parameters": tool.parameters,
            },
        }
        for tool in tools
    ]


def tool_specs(tools: list[ToolRef]) -> list[ToolSpec]:
    return [
        ToolSpec(
            name=tool.public_name,
            description=tool.description,
            parameters=tool.parameters,
        )
        for tool in tools
    ]


def find_tool(tools: list[ToolRef], public_name: str) -> ToolRef:
    for tool in tools:
        if tool.public_name == public_name:
            return tool
    raise KeyError(f"Tool {public_name} not found")


def parse_tool_arguments(arguments: str | dict[str, Any] | None) -> dict[str, Any]:
    if arguments is None:
        return {}
    if isinstance(arguments, dict):
        return arguments
    decoded = json.loads(arguments)
    if not isinstance(decoded, dict):
        raise ValueError("Tool call arguments must be a JSON object")
    return decoded


async def _collect_builtin_tools() -> list[ToolRef]:
    async with Client(arithmetic_mcp) as client:
        tools = await client.list_tools()
    return [
        ToolRef(
            public_name=f"arithmetic__{tool.name}",
            server_name="arithmetic",
            tool_name=tool.name,
            description=tool.description,
            parameters=tool.inputSchema,
        )
        for tool in tools
    ]


async def _collect_remote_tools(config: dict[str, Any]) -> list[ToolRef]:
    auth = BearerAuth(config["token"])
    async with Client(config["url"], auth=auth) as client:
        tools = await client.list_tools()
    prefix = _safe_prefix(config["name"])
    return [
        ToolRef(
            public_name=f"{prefix}__{tool.name}",
            server_name=config["name"],
            tool_name=tool.name,
            description=tool.description,
            parameters=tool.inputSchema,
            url=config["url"],
            token=config["token"],
        )
        for tool in tools
    ]


def _safe_prefix(value: str) -> str:
    chars = [char.lower() if char.isalnum() else "_" for char in value]
    prefix = "".join(chars).strip("_")
    return prefix or "mcp"
