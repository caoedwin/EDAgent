"""多 Agent 协作子图：「规划者 → 执行者 → 审稿者」分析小组。

图结构（TeamState 独立于主图 AgentState，经包装节点与主图衔接）：

    START -> planner -> worker <-> tools -> reviewer
                                   ^          |
                                   +--revise--+
                                              |--approve--> END

- planner：把复杂问题拆解为 2-4 个分析要点（内部消息，不直接面向用户）
- worker：复用主图的 ReAct 节点（可调用 calculator / knowledge_search 等工具）
- reviewer：审阅草稿；不合格且未超轮次时退回 worker 修订，最多 MAX_TEAM_ROUNDS 轮

worker 节点是 async + ainvoke，其 token 会以 langgraph_node="worker"
冒泡到主图的 astream_events（SSE 过滤集合需包含 "worker"）。
"""

from functools import lru_cache
from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app.agents.nodes import _last_user_question, agent_node, should_continue, tool_node
from app.agents.state import AgentState
from app.config import settings
from app.llm.client import chat_ainvoke
from app.observability.tracing import get_tracer


class TeamState(TypedDict, total=False):
    """协作小组内部状态（独立于主图，避免污染父图 schema）。"""

    messages: Annotated[list[BaseMessage], add_messages]
    tool_results: list[dict[str, Any]]
    iteration: int
    verdict: str


async def planner_node(state: TeamState, config: RunnableConfig) -> dict:
    with get_tracer().start_as_current_span("node_planner") as span:
        span.set_attribute("agent.node", "planner")
        question = _last_user_question(state)
        plan = await chat_ainvoke(
            [SystemMessage(content=settings.team_planner_prompt), HumanMessage(content=question)],
            temperature=0.2,
        )
        return {
            "messages": [SystemMessage(content=f"分析计划：\n{plan.content}")],
        }


async def reviewer_node(state: TeamState, config: RunnableConfig) -> dict:
    with get_tracer().start_as_current_span("node_reviewer") as span:
        span.set_attribute("agent.node", "reviewer")
        iteration = int(state.get("iteration") or 0) + 1
        span.set_attribute("agent.review_iteration", iteration)

        question = _last_user_question(state)
        draft = str(state["messages"][-1].content)
        plan_text = next(
            (str(m.content) for m in reversed(state["messages"])
             if isinstance(m, SystemMessage) and str(m.content).startswith("分析计划")),
            "",
        )
        review = await chat_ainvoke(
            [
                SystemMessage(
                    content=settings.team_reviewer_prompt.format(
                        iteration=iteration, max_rounds=settings.team_max_rounds
                    )
                ),
                HumanMessage(content=f"用户问题：\n{question}\n\n{plan_text}\n\n草稿：\n{draft}"),
            ],
            temperature=0,
        )
        verdict, notes = _parse_review(str(review.content))
        # 达到轮次上限强制放行
        if iteration >= settings.team_max_rounds:
            verdict = "approve"

        if verdict == "revise":
            return {
                "iteration": iteration,
                "verdict": "revise",
                "messages": [
                    SystemMessage(content=f"审稿意见（第 {iteration} 轮）：{notes}\n请据此修订你的回答。")
                ],
            }
        return {"iteration": iteration, "verdict": "approve"}


def _parse_review(text: str) -> tuple[str, str]:
    import json
    import re

    try:
        match = re.search(r"\{[^{}]*\}", text, flags=re.S)
        if match:
            data = json.loads(match.group(0))
            verdict = data.get("verdict", "approve")
            return (
                verdict if verdict in {"approve", "revise"} else "approve",
                str(data.get("notes", "")),
            )
    except Exception:
        pass
    return "approve", ""


def _team_route(state: TeamState) -> str:
    return "revise" if state.get("verdict") == "revise" else "approve"


@lru_cache(maxsize=1)
def build_team_graph():
    """编译协作小组子图（无独立 checkpointer，持久化由主图负责）。"""
    graph = StateGraph(TeamState)

    graph.add_node("planner", planner_node)
    graph.add_node("worker", agent_node)  # 复用主图 ReAct 节点
    graph.add_node("tools", tool_node)
    graph.add_node("reviewer", reviewer_node)

    graph.add_edge(START, "planner")
    graph.add_edge("planner", "worker")
    graph.add_conditional_edges(
        "worker",
        should_continue,
        {"tools": "tools", "report": "reviewer"},  # 主图 should_continue 返回 "report" 表达"草稿完成"
    )
    graph.add_edge("tools", "worker")
    graph.add_conditional_edges(
        "reviewer",
        _team_route,
        {"revise": "worker", "approve": END},
    )
    return graph.compile()


async def team_node(state: AgentState, config: RunnableConfig) -> dict:
    """主图中的包装节点：驱动子图并把最终草稿回填到主图状态。

    config 透传保证 configurable.user_id（知识库隔离）与 callbacks
    （token 级事件冒泡到主图 astream_events）继续生效。
    """
    with get_tracer().start_as_current_span("node_team") as span:
        span.set_attribute("agent.node", "team")
        result = await build_team_graph().ainvoke(
            {
                "messages": list(state.get("messages", [])),
                "tool_results": list(state.get("tool_results") or []),
                "iteration": 0,
            },
            config=config,
        )
        return {
            "messages": [result["messages"][-1]],  # 审稿放行后的最终 AI 草稿
            "tool_results": result.get("tool_results") or [],
        }
