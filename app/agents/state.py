"""LangGraph Agent 状态定义（技能规范：必须含 messages/next_action/tool_results/session_id）。"""

from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    # add_messages reducer：自动追加消息并按 id 去重
    messages: Annotated[list[BaseMessage], add_messages]
    # 路由结果：simple | tool | rag
    next_action: str
    # 工具执行记录（名称 / 入参 / 状态 / 结果摘要）
    tool_results: list[dict[str, Any]]
    # 业务检查点标识（对应 LangGraph thread_id，可从该检查点恢复）
    session_id: str
    # RAG 检索到的引用来源
    retrieved_sources: list[dict[str, Any]]
    # Report 节点产出的结构化最终结果
    final_report: dict[str, Any]
