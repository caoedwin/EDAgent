"""Web UI 专属接口：历史会话管理 + SSE 流式聊天。

SSE 事件协议（fetch + ReadableStream 在前端手动解析）：
    event: meta       data: {conversation_id, title}
    event: reasoning  data: {delta}   # 思考过程增量
    event: token      data: {delta}   # 正文增量
    event: done       data: {answer, route, sources, tool_results}
    event: error      data: {message}
"""

import asyncio
import json
import logging
import traceback
import uuid
from datetime import datetime
from functools import partial
from typing import Any
from urllib.parse import quote

from anyio import to_thread
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app import db
from app.config import settings
from app.security.web_auth import require_login

logger = logging.getLogger("edagent.ui")

router = APIRouter(prefix="/api/v1/ui", tags=["ui"])

# 这些图节点中的 LLM 输出才推给前端
# （router 的路由 JSON、planner/reviewer 的内部协作消息属于内部调用；
#   "worker" 是多 Agent 子图中的执行者节点，其 token 即最终草稿）
_STREAM_NODES = {"direct_answer", "agent", "worker"}


class ChatStreamRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=100)


def _sse(event: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


@router.get("/conversations")
def list_conversations(
    request: Request,
    q: str | None = None,
    user: dict = Depends(require_login),
) -> dict:
    return {"items": db.list_conversations(request.app.state.pool, user["id"], query=q)}


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


def _build_export_markdown(conv: dict, messages: list[dict]) -> str:
    """把会话构建为 Markdown 文本。"""
    lines: list[str] = [
        f"# {conv['title']}",
        "",
        f"> 导出时间：{datetime.now():%Y-%m-%d %H:%M}  ",
        f"> 消息数：{len(messages)}",
        "",
    ]
    for message in messages:
        who = "我" if message["role"] == "user" else "EdAgent"
        created = str(message["created_at"])[:19].replace("T", " ")
        lines += [f"## {who}", "", f"- 时间：{created}"]
        if message.get("route"):
            lines.append(f"- 路由：{message['route']}")
        lines.append("")

        reasoning = message.get("reasoning")
        if reasoning:
            lines += ["<details><summary>思考过程</summary>", "", reasoning, "", "</details>", ""]

        lines += [message["content"], ""]

        for tool in message.get("tool_results") or []:
            status = "成功" if tool.get("status") == "success" else "失败"
            lines.append(f"- 工具 `{tool.get('tool')}`（{status}）：{tool.get('output', '')[:200]}")
        sources = message.get("sources") or []
        if sources:
            lines += ["", "参考来源："]
            seen: set[str] = set()
            for src in sources:
                key = f"{src.get('source')}-{src.get('page') or 0}"
                if key in seen:
                    continue
                seen.add(key)
                page = f" 第 {src['page']} 页" if src.get("page") else ""
                lines.append(f"- {src.get('original_name') or src.get('source')}{page}")
        lines += ["", "---", ""]

    return "\n".join(lines).rstrip() + "\n"


def _markdown_to_html(markdown_text: str, title: str) -> str:
    """把 Markdown 转为自包含 HTML 页面（内联 CSS，可直接打印为 PDF）。"""
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"html": True}).enable("table")
    body_html = md.render(markdown_text)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
body {{ font-family: -apple-system, "Segoe UI", Roboto, "Noto Sans SC", sans-serif; max-width: 820px; margin: 40px auto; padding: 0 20px; line-height: 1.7; color: #1e293b; }}
h1, h2, h3 {{ color: #0f172a; }}
h1 {{ border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; }}
blockquote {{ border-left: 4px solid #3b82f6; margin: 0; padding: 4px 16px; color: #64748b; background: #f8fafc; }}
code {{ background: #f1f5f9; padding: 2px 5px; border-radius: 4px; font-size: 0.9em; }}
pre {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px; overflow-x: auto; }}
details {{ margin: 8px 0; padding: 8px 12px; background: #f8fafc; border: 1px solid #edf0f6; border-radius: 8px; }}
summary {{ cursor: pointer; color: #64748b; font-size: 0.9em; }}
hr {{ border: none; border-top: 1px solid #e2e8f0; margin: 24px 0; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #e2e8f0; padding: 6px 10px; }}
th {{ background: #f8fafc; }}
</style>
</head>
<body>
{body_html}
</body>
</html>"""


@router.get("/conversations/{conversation_id}/export")
def export_conversation(
    conversation_id: str,
    request: Request,
    user: dict = Depends(require_login),
    format: str = "md",
):
    """导出会话为 Markdown 或 HTML 文件。format=md 返回 .md，format=html 返回自包含 HTML 页面。"""
    try:
        uuid.UUID(conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc

    conv = db.get_owned_conversation(request.app.state.pool, conversation_id, user["id"])
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = db.list_messages(request.app.state.pool, conversation_id)

    markdown = _build_export_markdown(conv, messages)
    safe_title = quote(conv["title"] or "conversation")[:60]

    if format == "html":
        html = _markdown_to_html(markdown, conv["title"])
        return HTMLResponse(
            html,
            headers={
                "Content-Disposition": (
                    f"attachment; filename=conversation-{conversation_id[:8]}.html; "
                    f"filename*=UTF-8''{safe_title}.html"
                )
            },
        )

    # 默认 markdown
    return PlainTextResponse(
        markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=conversation-{conversation_id[:8]}.md; "
                f"filename*=UTF-8''{safe_title}.md"
            )
        },
    )


@router.post("/conversations/{conversation_id}/pin")
def pin_conversation(
    conversation_id: str, request: Request, user: dict = Depends(require_login)
) -> dict:
    """切换会话置顶状态。"""
    result = db.toggle_pin(request.app.state.pool, conversation_id, user["id"])
    if result is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True, "pinned": result}


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
    """SSE 流式聊天。

    采用「生产者任务 + 消费者生成器」结构：
    - producer 任务驱动 LangGraph astream_events，把 SSE 帧写入 asyncio.Queue；
    - 生成器从队列取帧下发，每秒轮询 request.is_disconnected()；
    - 客户端中断（前端 stop 按钮 abort / 关闭页面）时取消 producer，
      图执行与 LLM 调用随之终止，并尽力把已生成的部分答案落库。
    """
    pool = request.app.state.pool
    graph = request.app.state.graph
    owner = f"user-{user['id']}"

    # producer 与清理逻辑共享的收集器（中断时用于持久化部分结果）
    collected: dict[str, Any] = {
        "conv": None,
        "answer": [],
        "reasoning": [],
        "saved": False,
    }

    async def producer(queue: Any) -> None:
        try:
            # 1) 定位或创建会话（严格校验归属）
            if payload.conversation_id:
                try:
                    uuid.UUID(payload.conversation_id)
                except ValueError:
                    queue.put_nowait(_sse("error", {"message": "会话不存在"}))
                    return
                conv = await to_thread.run_sync(
                    db.get_owned_conversation, pool, payload.conversation_id, user["id"]
                )
                if conv is None:
                    queue.put_nowait(_sse("error", {"message": "会话不存在或无权访问"}))
                    return
            else:
                title = payload.message.strip().splitlines()[0][:24] or "新会话"
                conv = await to_thread.run_sync(
                    db.create_conversation, pool, user["id"], title
                )
            collected["conv"] = conv

            queue.put_nowait(_sse("meta", {"conversation_id": conv["id"], "title": conv["title"]}))
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
                    collected["reasoning"].append(reasoning)
                    queue.put_nowait(_sse("reasoning", {"delta": reasoning}))
                if text:
                    collected["answer"].append(text)
                    queue.put_nowait(_sse("token", {"delta": text}))

            # 3) 取结构化最终报告并落库
            state_snapshot = await graph.aget_state(config)
            report = (state_snapshot.values or {}).get("final_report") or {}
            answer = report.get("answer") or "".join(collected["answer"]) or "（模型未返回内容）"
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
                    reasoning="".join(collected["reasoning"]) or None,
                    route=route,
                    sources=sources,
                    tool_results=tool_results,
                )
            )
            collected["saved"] = True

            queue.put_nowait(
                _sse(
                    "done",
                    {
                        "answer": answer,
                        "route": route,
                        "sources": sources,
                        "tool_results": tool_results,
                        "model": settings.llm_model,
                    },
                )
            )
        except Exception as exc:  # LLM / 图执行失败
            tb = traceback.format_exc()
            logger.warning("chat stream failed: %s\n%s", repr(exc), tb)
            queue.put_nowait(_sse("error", {"message": f"生成失败：{type(exc).__name__}: {exc}"}))
        finally:
            queue.put_nowait(None)  # 结束哨兵

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()
        producer_task = asyncio.create_task(producer(queue))
        interrupted = False
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        interrupted = True
                        break
                    continue
                if item is None:
                    break
                yield item
        finally:
            # 客户端断开（或生成器被取消）时终止图执行并持久化部分答案
            if not producer_task.done():
                interrupted = True
                producer_task.cancel()
            conv = collected.get("conv")
            if interrupted and conv and not collected["saved"]:
                answer = "".join(collected["answer"]).strip()
                if answer:
                    try:
                        db.add_message(
                            pool,
                            conv["id"],
                            "assistant",
                            answer,
                            reasoning="".join(collected["reasoning"]).strip() or None,
                        )
                    except Exception:  # 清理阶段落库失败不影响连接关闭
                        logger.exception("persist partial answer failed")
            # 等待 producer 收尾，避免任务泄漏（吞掉取消异常）
            try:
                await producer_task
            except (asyncio.CancelledError, Exception):
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 告知 Nginx 不要缓冲 SSE
        },
    )
