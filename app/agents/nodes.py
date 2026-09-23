"""LangGraph 节点实现：Router / 直接回答 / ReAct(agent) / Tool / RAG / Report。

每个节点创建带 agent.node 属性的 span；LLM 调用统一走 tracked_invoke
（gen_ai.* 追踪 + Prometheus 指标 + Token 统计）。
"""

import json
import re
from functools import lru_cache

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from app.agents.state import AgentState
from app.agents.tools import TOOL_MAP, TOOLS
from app.config import settings
from app.llm.client import chat_ainvoke, get_chat_model, tracked_ainvoke
from app.observability.metrics import ERRORS, TOOL_CALLS
from app.observability.tracing import get_tracer
from app.rag.ingest import kb_document_count
from app.rag.retriever import format_context, retrieve

SYSTEM_PROMPT = (
    "你是 EdAgent，一个运行在用户本地的 AI 助手。请遵循以下要求：\n"
    "1. 使用中文回答，内容准确、条理清晰；\n"
    "2. 需要精确计算、当前时间或字数统计时，必须调用提供的工具，不要心算；\n"
    "3. 需要私有知识时使用 knowledge_search 工具，并依据检索到的资料作答、标注来源；\n"
    "4. 工具返回结果是事实依据，不要编造工具输出；\n"
    "5. 任务完成后直接给出最终答案，不要解释你的内部推理过程。"
)

ROUTER_PROMPT = """你是任务路由器。分析用户请求，判断处理路径。

可选路径：
- simple：常识问答、写作、翻译、总结、闲聊等，可直接由 LLM 回答
- tool：需要精确数学计算、当前时间、字数统计等工具能力
- rag：必须依赖本地私有知识库文档中的信息才能回答

当前本地知识库文档分块数量：{kb_chunks}（数量为 0 时禁止选择 rag）。

只输出一行 JSON，不要输出任何其他内容：
{{"route": "simple|tool|rag", "reason": "一句话中文理由"}}"""


def _last_user_question(state: AgentState) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


def _current_user_id(config: RunnableConfig | None) -> str | None:
    """从图运行配置中提取当前用户标识（由 API 层注入 configurable.user_id）。"""
    return ((config or {}).get("configurable") or {}).get("user_id")


def _parse_route(text: str) -> str:
    try:
        match = re.search(r"\{[^{}]*\}", text, flags=re.S)
        if not match:
            return "simple"
        route = json.loads(match.group(0)).get("route", "simple")
        return route if route in {"simple", "tool", "rag"} else "simple"
    except Exception:
        return "simple"


async def router_node(state: AgentState, config: RunnableConfig) -> dict:
    with get_tracer().start_as_current_span("node_router") as span:
        span.set_attribute("agent.node", "router")
        user_id = _current_user_id(config)
        kb_chunks = kb_document_count(user_id)
        prompt = ROUTER_PROMPT.format(kb_chunks=kb_chunks)
        response = await chat_ainvoke(
            [SystemMessage(content=prompt), HumanMessage(content=_last_user_question(state))],
            temperature=0,
        )
        route = _parse_route(str(response.content))
        # 知识库为空时强制不走 RAG
        if route == "rag" and kb_chunks == 0:
            route = "simple"
        span.set_attribute("agent.route", route)
        span.set_attribute("agent.kb_chunks", kb_chunks)
        return {"next_action": route}


async def direct_answer_node(state: AgentState) -> dict:
    with get_tracer().start_as_current_span("node_direct_answer") as span:
        span.set_attribute("agent.node", "direct_answer")
        messages = [SystemMessage(content=SYSTEM_PROMPT), *state.get("messages", [])]
        response = await chat_ainvoke(messages)
        return {"messages": [response]}


def retrieve_node(state: AgentState, config: RunnableConfig) -> dict:
    with get_tracer().start_as_current_span("node_retrieve") as span:
        span.set_attribute("agent.node", "retrieve")
        question = _last_user_question(state)
        documents = retrieve(question, _current_user_id(config))
        span.set_attribute("agent.retrieved_docs", len(documents))

        sources = [
            {
                "source": doc.metadata.get("source"),
                "page": doc.metadata.get("page") or None,
                "original_name": doc.metadata.get("original_name"),
            }
            for doc in documents
        ]
        if documents:
            content = (
                "已从本地知识库检索到以下资料，请优先依据这些资料回答用户问题，"
                f"并在回答末尾用“参考来源”列出引用的文件名与页码：\n\n{format_context(documents)}"
            )
        else:
            content = "本地知识库中未检索到相关资料，请明确告知用户这一点，不要编造。"
        return {
            "messages": [SystemMessage(content=content)],
            "retrieved_sources": sources,
        }


@lru_cache(maxsize=1)
def _tool_enabled_llm():
    # 显式启用工具调用；后端模型不支持 tools 时会自然退化为直接回答
    return get_chat_model(temperature=0.2).bind_tools(TOOLS)


async def agent_node(state: AgentState) -> dict:
    """ReAct 中的“思考-决策”节点：模型决定调用工具或给出最终答案。"""
    with get_tracer().start_as_current_span("node_agent") as span:
        span.set_attribute("agent.node", "agent")
        messages = [SystemMessage(content=SYSTEM_PROMPT), *state.get("messages", [])]
        response = await tracked_ainvoke(_tool_enabled_llm(), messages, operation="chat")
        return {"messages": [response]}


def tool_node(state: AgentState) -> dict:
    """ReAct 中的“行动”节点：在进程内安全执行工具（无容器/Shell 依赖）。"""
    with get_tracer().start_as_current_span("node_tools") as span:
        span.set_attribute("agent.node", "tools")
        last_message = state["messages"][-1]
        new_messages: list[ToolMessage] = []
        executed: list[dict] = []

        for call in getattr(last_message, "tool_calls", None) or []:
            name = call.get("name", "unknown")
            args = call.get("args") or {}
            span.set_attribute(f"agent.tool.{name}", True)
            try:
                tool_obj = TOOL_MAP.get(name)
                if tool_obj is None:
                    output = f"未知工具: {name}"
                    status = "error"
                else:
                    output = tool_obj.invoke(args)
                    status = "success"
            except Exception as exc:
                output = f"工具执行失败: {exc}"
                status = "error"
                ERRORS.labels(route="agent", type="tool").inc()

            TOOL_CALLS.labels(tool=name, status=status).inc()
            executed.append({"tool": name, "args": args, "status": status, "output": str(output)[:1000]})
            new_messages.append(
                ToolMessage(content=str(output), tool_call_id=call.get("id", name), name=name)
            )

        return {
            "messages": new_messages,
            "tool_results": [*(state.get("tool_results") or []), *executed],
        }


def _strip_think_tags(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()


def report_node(state: AgentState) -> dict:
    """结构化输出生成：清洗推理痕迹并汇总工具调用与引用来源。"""
    with get_tracer().start_as_current_span("node_report") as span:
        span.set_attribute("agent.node", "report")
        final_text = _strip_think_tags(str(state["messages"][-1].content))
        if not final_text:
            # 个别后端会把正文放在其他 AI 消息中，向前回溯一条非空内容兜底
            for message in reversed(state["messages"][:-1]):
                if isinstance(message, AIMessage) and message.content:
                    final_text = _strip_think_tags(str(message.content))
                    break

        return {
            "final_report": {
                "answer": final_text or "（模型未返回文本内容）",
                "route": state.get("next_action"),
                "tool_results": state.get("tool_results") or [],
                "sources": state.get("retrieved_sources") or [],
                "model": settings.llm_model,
            }
        }


def should_continue(state: AgentState) -> str:
    """ReAct 循环条件：还有工具调用则进 tools，否则结束并输出报告。"""
    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        return "tools"
    return "report"
