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
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app import db
from app.api.broadcast import hub
from app.config import settings
from app.security.web_auth import require_login, session_user_id

logger = logging.getLogger("edagent.ui")

router = APIRouter(prefix="/api/v1/ui", tags=["ui"])

# 这些图节点中的 LLM 输出才推给前端
# （router 的路由 JSON、planner/reviewer 的内部协作消息属于内部调用；
#   "worker" 是多 Agent 子图中的执行者节点，其 token 即最终草稿）
_STREAM_NODES = {"direct_answer", "agent", "worker"}

# 标签 / 分类的清洗上限
_MAX_TAGS = 10
_MAX_TAG_LEN = 24
_MAX_CATEGORY_LEN = 32


class ChatStreamRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=100)


class ConversationMetaUpdate(BaseModel):
    """会话组织维度局部更新；请求体中未出现的字段保持不变。"""

    tags: list[str] | None = None
    category: str | None = None
    favorite: bool | None = None


def _ensure_uuid(value: str) -> str:
    """校验 UUID 字符串；非法时按「资源不存在」处理。"""
    try:
        uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc
    return value


def _normalize_tags(tags: list[str]) -> list[str]:
    """清洗标签：去首尾空白、去重、限长限量。"""
    cleaned: list[str] = []
    for raw in tags:
        name = str(raw).strip()[:_MAX_TAG_LEN]
        if name and name not in cleaned:
            cleaned.append(name)
    return cleaned[:_MAX_TAGS]


def _sse(event: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


@router.get("/conversations")
def list_conversations(
    request: Request,
    q: str | None = None,
    tag: str | None = None,
    category: str | None = None,
    favorite: int = 0,
    user: dict = Depends(require_login),
) -> dict:
    """会话列表：支持标题模糊搜索、标签 / 分类筛选与「仅收藏」过滤。"""
    return {
        "items": db.list_conversations(
            request.app.state.pool,
            user["id"],
            query=q,
            tag=tag,
            category=category,
            favorite=bool(favorite),
        )
    }


@router.get("/conversation-filters")
def conversation_filters(request: Request, user: dict = Depends(require_login)) -> dict:
    """当前用户已用过的分类与标签（含计数），供侧栏筛选下拉使用。"""
    return db.list_conversation_filters(request.app.state.pool, user["id"])


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


# ==================== 会话组织维度：标签 / 分类 / 收藏 ====================


@router.patch("/conversations/{conversation_id}/meta")
def update_conversation_meta(
    conversation_id: str,
    payload: ConversationMetaUpdate,
    request: Request,
    user: dict = Depends(require_login),
) -> dict:
    """局部更新会话的标签 / 分类 / 收藏。

    仅更新请求体中显式出现的字段：tags 传 `[]` 清空标签，category 传空串清空分类。
    """
    _ensure_uuid(conversation_id)
    provided = payload.model_dump(exclude_unset=True)

    changes: dict[str, Any] = {}
    if "tags" in provided:
        changes["tags"] = _normalize_tags(provided["tags"] or [])
    if "category" in provided:
        category = (provided["category"] or "").strip()[:_MAX_CATEGORY_LEN]
        changes["category"] = category or None
    if "favorite" in provided:
        changes["favorite"] = bool(provided["favorite"])

    conv = db.update_conversation_meta(
        request.app.state.pool, conversation_id, user["id"], changes
    )
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True, "conversation": conv}


# ==================== 协作围观：分享链接 ====================


@router.get("/conversations/{conversation_id}/share")
def get_conversation_share(
    conversation_id: str, request: Request, user: dict = Depends(require_login)
) -> dict:
    """读取会话分享状态（供分享弹窗回显）。"""
    _ensure_uuid(conversation_id)
    token = db.get_share_token(request.app.state.pool, conversation_id, user["id"])
    if token is None and (
        db.get_owned_conversation(request.app.state.pool, conversation_id, user["id"])
        is None
    ):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {
        "ok": True,
        "enabled": token is not None,
        "share_token": token,
        "share_url": f"/?c={conversation_id}&share={token}" if token else None,
        "viewers": hub.viewer_count(token) if token else 0,
    }


@router.post("/conversations/{conversation_id}/share")
def create_conversation_share(
    conversation_id: str, request: Request, user: dict = Depends(require_login)
) -> dict:
    """开启协作围观：生成（或沿用）分享 token，任何登录用户凭链接只读实时围观。"""
    _ensure_uuid(conversation_id)
    token = db.enable_share(request.app.state.pool, conversation_id, user["id"])
    if token is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {
        "ok": True,
        "share_token": token,
        "share_url": f"/?c={conversation_id}&share={token}",
        "viewers": hub.viewer_count(token),
    }


@router.delete("/conversations/{conversation_id}/share")
async def revoke_conversation_share(
    conversation_id: str, request: Request, user: dict = Depends(require_login)
) -> dict:
    """关闭协作围观：清空 token 并解散已打开的围观连接。"""
    _ensure_uuid(conversation_id)
    pool = request.app.state.pool
    token = await to_thread.run_sync(
        db.get_share_token, pool, conversation_id, user["id"]
    )
    if not await to_thread.run_sync(db.disable_share, pool, conversation_id, user["id"]):
        raise HTTPException(status_code=404, detail="会话不存在")
    if token:
        await hub.close_room(
            token, {"type": "error", "message": "发起人已关闭协作围观"}
        )
    return {"ok": True}


@router.get("/shared/{share_token}")
def get_shared_conversation(
    share_token: str, request: Request, user: dict = Depends(require_login)
) -> dict:
    """围观者读取共享会话（只读，含历史消息）。"""
    conv = db.get_shared_conversation(request.app.state.pool, share_token)
    if conv is None:
        raise HTTPException(status_code=404, detail="分享链接无效或已关闭")
    conv["messages"] = db.list_messages(request.app.state.pool, conv["id"])
    return conv


@router.websocket("/ws/{share_token}")
async def collaborate(websocket: WebSocket, share_token: str) -> None:
    """协作围观 WebSocket：接入某会话的实时房间。

    下发事件类型：
      history    加入时的历史消息与发起人
      presence   在线成员列表（去重）
      join/leave 成员进出提示
      turn_start 发起人提问（围观者插入新的一轮）
      reasoning / token / turn_done / turn_error   AI 输出事件
    只读语义：客户端上行文本仅作为心跳，内容被忽略。
    """
    pool = websocket.app.state.pool
    user_id = session_user_id(websocket)
    # 先 accept 再校验：未 accept 时 close() 只能让客户端收到 403（无自定义关闭码），
    # accept 后关闭可携带 44xx 语义码，前端据此给出明确提示
    await websocket.accept()
    if user_id is None:
        await websocket.send_json({"type": "error", "message": "登录已失效，请重新登录"})
        await websocket.close(code=4401)
        return

    user = await to_thread.run_sync(db.get_user, pool, user_id)
    conv = await to_thread.run_sync(db.get_shared_conversation, pool, share_token)
    if user is None or conv is None:
        await websocket.send_json(
            {"type": "error", "message": "分享链接无效或已被关闭"}
        )
        await websocket.close(code=4404)
        return

    member = {"id": user["id"], "username": user["username"]}
    await hub.join(share_token, websocket, member)
    try:
        messages = await to_thread.run_sync(db.list_messages, pool, conv["id"])
        await websocket.send_json(
            {
                "type": "history",
                "conversation_id": conv["id"],
                "title": conv["title"],
                "owner": conv["owner_username"],
                "messages": messages,
            }
        )
        await hub.broadcast(
            share_token, {"type": "presence", "members": hub.members(share_token)}
        )
        await hub.broadcast(
            share_token,
            {"type": "join", "username": member["username"]},
            exclude=websocket,
        )
        while True:
            await websocket.receive_text()  # 只读房间：上行内容忽略，仅用于探活
    except WebSocketDisconnect:
        pass
    except Exception:  # 非预期断开：清理连接即可，不影响其他围观者
        logger.debug("协作 WebSocket 异常关闭 room=%s", share_token, exc_info=True)
    finally:
        await hub.leave(share_token, websocket)
        await hub.broadcast(
            share_token, {"type": "presence", "members": hub.members(share_token)}
        )
        await hub.broadcast(
            share_token, {"type": "leave", "username": member["username"]}
        )


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
        "room": None,
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
            # 已开启协作围观时，同步把本轮问答广播到围观房间
            room = conv.get("share_token")
            collected["room"] = room

            queue.put_nowait(_sse("meta", {"conversation_id": conv["id"], "title": conv["title"]}))
            await to_thread.run_sync(
                db.add_message, pool, conv["id"], "user", payload.message
            )
            if room:
                await hub.broadcast(
                    room,
                    {
                        "type": "turn_start",
                        "conversation_id": conv["id"],
                        "username": user["username"],
                        "message": payload.message,
                    },
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
                    if room:
                        await hub.broadcast(
                            room, {"type": "reasoning", "delta": reasoning}
                        )
                if text:
                    collected["answer"].append(text)
                    queue.put_nowait(_sse("token", {"delta": text}))
                    if room:
                        await hub.broadcast(room, {"type": "token", "delta": text})

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
            if room:
                await hub.broadcast(
                    room,
                    {
                        "type": "turn_done",
                        "answer": answer,
                        "route": route,
                        "sources": sources,
                        "tool_results": tool_results,
                    },
                )
        except Exception as exc:  # LLM / 图执行失败
            tb = traceback.format_exc()
            logger.warning("chat stream failed: %s\n%s", repr(exc), tb)
            message = f"生成失败：{type(exc).__name__}: {exc}"
            queue.put_nowait(_sse("error", {"message": message}))
            room = collected.get("room")
            if room:
                await hub.broadcast(room, {"type": "turn_error", "message": message})
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
            room = collected.get("room")
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
                if room:
                    # 发起人中断生成：同步告知围观者本轮结束（保留已生成的部分内容）
                    await hub.broadcast(
                        room,
                        {
                            "type": "turn_done",
                            "answer": answer,
                            "route": None,
                            "sources": [],
                            "tool_results": [],
                            "aborted": True,
                        },
                    )
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
