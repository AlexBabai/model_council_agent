from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

Role = Literal["system", "user", "assistant", "tool"]


class LlmConfigCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: HttpUrl
    api_key: str = Field(min_length=1)
    model: str = Field(min_length=1, max_length=200)


class LlmConfigPublic(BaseModel):
    id: int
    name: str
    base_url: str
    model: str


class McpConfigCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: HttpUrl
    token: str = Field(min_length=1)


class McpConfigPublic(BaseModel):
    id: int
    name: str
    url: str


class ChatCreate(BaseModel):
    title: str = Field(default="New chat", min_length=1, max_length=200)
    llm_config_ids: list[int] = Field(min_length=1, max_length=3)


class ChatPublic(BaseModel):
    id: int
    title: str
    llm_config_ids: list[int]
    attached_mcp_ids: list[int]
    created_at: str


class ChatMessageCreate(BaseModel):
    content: str = Field(min_length=1)
    stream: bool = False


class MessagePublic(BaseModel):
    id: int
    role: Role
    content: str
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatHistory(BaseModel):
    chat: ChatPublic
    messages: list[MessagePublic]


class ChatMcpUpdate(BaseModel):
    mcp_config_ids: list[int] = Field(default_factory=list)
    include_arithmetic: bool = True


class AgentResponse(BaseModel):
    chat_id: int
    message: MessagePublic
    completions_used: int
    model_votes: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]


class ChatCompletionMessage(BaseModel):
    role: Role
    content: str


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    chat_id: int | None = None
    llm_config_ids: list[int] | None = Field(default=None, min_length=1, max_length=3)
    messages: list[ChatCompletionMessage] = Field(min_length=1)
    stream: bool = False


class OpenAIChoice(BaseModel):
    index: int
    message: ChatCompletionMessage
    finish_reason: str


class OpenAIChatCompletion(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[OpenAIChoice]
    usage: dict[str, int]


class ToolSpec(BaseModel):
    name: str
    description: str | None = None
    parameters: dict[str, Any]
