"""LangGraph 状态机组装。

图结构：
    START -> router
      router --simple--> direct_answer -> report
      router --rag-----> retrieve -> agent <-> tools -> report
      router --tool----> agent <-> tools -> report
      router --team----> team（多Agent子图：planner -> worker<->tools -> reviewer）-> report

Postgres checkpointer 使每个节点后的状态持久化，可用同一 thread_id 多轮对话/恢复。
"""

from langgraph.graph import END, START, StateGraph

from app.agents.multi_agent import team_node
from app.agents.nodes import (
    agent_node,
    direct_answer_node,
    report_node,
    retrieve_node,
    router_node,
    should_continue,
    tool_node,
)
from app.agents.state import AgentState


def build_agent_graph(checkpointer=None):
    graph = StateGraph(AgentState)

    graph.add_node("router", router_node)
    graph.add_node("direct_answer", direct_answer_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.add_node("team", team_node)
    graph.add_node("report", report_node)

    graph.add_edge(START, "router")
    graph.add_conditional_edges(
        "router",
        lambda state: state.get("next_action", "simple"),
        {
            "simple": "direct_answer",
            "tool": "agent",
            "rag": "retrieve",
            "team": "team",
        },
    )
    graph.add_edge("direct_answer", "report")
    graph.add_edge("retrieve", "agent")
    graph.add_edge("team", "report")
    graph.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", "report": "report"},
    )
    graph.add_edge("tools", "agent")
    graph.add_edge("report", END)

    return graph.compile(checkpointer=checkpointer)
