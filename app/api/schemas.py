"""API 请求 / 响应模型。"""

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, description="用户消息")
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


class ChatResponse(BaseModel):
    answer: str
    model: str


class AgentRequest(BaseModel):
    message: str = Field(min_length=1, description="用户消息")
    thread_id: str | None = Field(default=None, description="对话线程 ID；传入相同 ID 可延续历史并从检查点恢复")


class AgentResponse(BaseModel):
    thread_id: str
    answer: str
    route: str | None
    tool_results: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    model: str


class RagQueryRequest(BaseModel):
    query: str = Field(min_length=1)
    answer: bool = Field(default=True, description="True=基于检索结果生成回答；False=仅返回检索片段")


class RagDocumentResponse(BaseModel):
    filename: str
    original_name: str
    pages: int
    chunks: int


class RagQueryResponse(BaseModel):
    query: str
    answer: str | None
    sources: list[dict[str, Any]]
    model: str | None
