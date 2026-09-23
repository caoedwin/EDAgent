"""Web UI 专属接口：历史会话管理 + SSE 流式聊天。

SSE 事件协议（fetch + ReadableStream 在前端手动解析）：
    event: meta       data: {conversation_id, title}
    event: reasoning  data: {delta}   # 思考过程增量
    event: token      data: {delta}   # 正文增量
    event: done       data: {answer, route, sources, tool_results}
    event: error      data: {message}
"""

import json
import logging
import traceback
import uuid
from functools import partial
from typing import Any

from anyio import to_thread
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app import db
from app.config import settings
from app.security.web_auth import require_login

logger = logging.getLogger("edagent.ui")

router = APIRouter(prefix="/api/v1/ui", tags=["ui"])

# 这些图节点中的 LLM 输出才推给前端（router 节点的路由 JSON 属于内部调用）
_STREAM_NODES = {"direct_answer", "agent"}


class ChatStreamRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=100)


def _sse(event: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


@router.get("/conversations")
def list_conversations(request: Request, user: dict = Depends(require_login)) -> dict:
    return {"items": db.list_conversations(request.app.state.pool, user["id"])}


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: str, request: Request, user: dict = Depends(require_login)
) -> dict:
    try:
        uuid.UUID(conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc

    conv = db.get_owned_conversation(request.app.state.pool, conversation_id, user["id"])
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    conv["messages"] = db.list_messages(request.app.state.pool, conversation_id)
    return conv


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: str, request: Request, user: dict = Depends(require_login)
) -> dict:
    deleted = db.delete_conversation(request.app.state.pool, conversation_id, user["id"])
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True}


@router.patch("/conversations/{conversation_id}")
def rename_conversation(
    conversation_id: str,
    payload: ConversationUpdate,
    request: Request,
    user: dict = Depends(require_login),
) -> dict:
    conv = db.get_owned_conversation(request.app.state.pool, conversation_id, user["id"])
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    with request.app.state.pool.connection() as conn:
        conn.execute(
            "UPDATE conversations SET title = %s WHERE id = %s",
            (payload.title[:100], conversation_id),
        )
    return {"ok": True}


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatStreamRequest,
    request: Request,
    user: dict = Depends(require_login),
) -> StreamingResponse:
    pool = request.app.state.pool
    graph = request.app.state.graph
    owner = f"user-{user['id']}"

    async def event_generator():
        # 1) 定位或创建会话（严格校验归属）
        if payload.conversation_id:
            try:
                uuid.UUID(payload.conversation_id)
            except ValueError:
                yield _sse("error", {"message": "会话不存在"})
                return
            conv = await to_thread.run_sync(
                db.get_owned_conversation, pool, payload.conversation_id, user["id"]
            )
            if conv is None:
                yield _sse("error", {"message": "会话不存在或无权访问"})
                return
        else:
            title = payload.message.strip().splitlines()[0][:24] or "新会话"
            conv = await to_thread.run_sync(
                db.create_conversation, pool, user["id"], title
            )

        yield _sse("meta", {"conversation_id": conv["id"], "title": conv["title"]})
        await to_thread.run_sync(
            db.add_message, pool, conv["id"], "user", payload.message
        )

        # 2) 运行 LangGraph，转发 token 流
        config = {
            "configurable": {"thread_id": conv["thread_id"], "user_id": owner},
            "recursion_limit": settings.max_agent_steps * 2 + 4,
        }
        graph_input = {
            "messages": [HumanMessage(content=payload.message)],
            "session_id": conv["thread_id"],
            "tool_results": [],
        }

        answer_parts: list[str] = []
        reasoning_parts: list[str] = []
        try:
            async for event in graph.astream_events(
                graph_input, config=config, version="v2"
            ):
                if event.get("event") != "on_chat_model_stream":
                    continue
                node = (event.get("metadata") or {}).get("langgraph_node")
                if node not in _STREAM_NODES:
                    continue
                chunk = (event.get("data") or {}).get("chunk")
                if chunk is None:
                    continue
                text = chunk.content if isinstance(chunk.content, str) else ""
                reasoning = getattr(chunk, "reasoning_content", None) or (
                    chunk.additional_kwargs or {}
                ).get("reasoning_content")
                if reasoning:
                    reasoning_parts.append(reasoning)
                    yield _sse("reasoning", {"delta": reasoning})
                if text:
                    answer_parts.append(text)
                    yield _sse("token", {"delta": text})
        except Exception as exc:  # LLM / 图执行失败
            tb = traceback.format_exc()
            logger.warning("chat stream failed: %s\n%s", repr(exc), tb)
            yield _sse("error", {"message": f"生成失败：{type(exc).__name__}: {exc}"})
            return

        # 3) 取结构化最终报告并落库
        state_snapshot = await graph.aget_state(config)
        report = (state_snapshot.values or {}).get("final_report") or {}
        answer = report.get("answer") or "".join(answer_parts) or "（模型未返回内容）"
        sources = report.get("sources") or []
        tool_results = report.get("tool_results") or []
        route = report.get("route")

        await to_thread.run_sync(
            partial(
                db.add_message,
                pool,
                conv["id"],
                "assistant",
                answer,
                reasoning="".join(reasoning_parts) or None,
                route=route,
                sources=sources,
                tool_results=tool_results,
            )
        )

        yield _sse(
            "done",
            {
                "answer": answer,
                "route": route,
                "sources": sources,
                "tool_results": tool_results,
                "model": settings.llm_model,
            },
        )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 告知 Nginx 不要缓冲 SSE
        },
    )
