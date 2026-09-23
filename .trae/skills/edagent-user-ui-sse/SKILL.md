---
name: edagent-user-ui-sse
description: 为 LangGraph/FastAPI Agent 增加用户系统、Web UI 和 SSE 流式输出的实现与排错指南。当用户需要添加用户注册登录、会话历史持久化、Vue3 前端、SSE 打字机效果或 RAG 按用户隔离时使用。
---

# EdAgent 用户系统 + Web UI + SSE 流式构建指南

## 架构概览

```
Nginx (18088) → FastAPI agent-api (18080)
  ├── Cookie Session（UI 用户） + Bearer API Key（机主/脚本）双通道鉴权
  ├── Vue3 SPA（Vite 构建，FastAPI 同源托管 + history fallback）
  ├── SSE 流式聊天（astream_events 逐 token）
  ├── PostgreSQL：users / conversations / messages + LangGraph checkpoints
  └── ChromaDB：按 owner metadata 隔离的 RAG
```

## 关键决策与坑点

### 1. AsyncPostgresSaver（非 PostgresSaver）

**症状**：SSE 流式聊天返回 `NotImplementedError`，图执行立即失败。

**根因**：图通过 `astream_events` / `ainvoke` 异步驱动，但同步 `PostgresSaver` 不支持异步上下文。

**修复**：`app/memory/checkpointer.py` 必须使用 `AsyncPostgresSaver`：

```python
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

async def create_checkpointer() -> AsyncPostgresSaver:
    connection = await psycopg.AsyncConnection.connect(
        settings.database_url, autocommit=True, connect_timeout=10,
    )
    saver = AsyncPostgresSaver(connection)
    await saver.setup()
    return saver
```

`main.py` 的 `lifespan` 必须 `await create_checkpointer()`，所有调用图的路由必须 `async def` + `await graph.ainvoke(...)`。

### 2. SSE 流式事件协议

后端 `astream_events(version="v2")` 只转发 `on_chat_model_stream` 事件，且过滤 `metadata.langgraph_node` 属于 `{direct_answer, agent}`（排除 router 内部 JSON）：

```python
async for event in graph.astream_events(graph_input, config=config, version="v2"):
    if event["event"] != "on_chat_model_stream":
        continue
    node = event["metadata"].get("langgraph_node", "")
    if node not in ("direct_answer", "agent"):
        continue
    chunk = event["data"]["chunk"]
    reasoning = getattr(chunk, "reasoning_content", None) or chunk.additional_kwargs.get("reasoning_content")
    text = chunk.content if isinstance(chunk.content, str) else ""
    # → yield SSE: reasoning / token
```

**关键**：router/direct_answer/agent 三个节点必须改为 `async def` 并使用 `ainvoke`（非 `invoke`），否则 token 级事件不会冒泡。

### 3. anyio to_thread.run_sync 不支持 kwargs

**症状**：`TypeError: run_sync() got an unexpected keyword argument 'reasoning'`

**根因**：`to_thread.run_sync(func, *args, **kwargs)` 中 kwargs 不会透传给 func。

**修复**：用 `functools.partial` 包装：

```python
from functools import partial
await to_thread.run_sync(
    partial(db.add_message, pool, conv_id, "assistant", answer,
            reasoning=reasoning, route=route, sources=sources)
)
```

### 4. Chroma 多键 where 过滤需 $and

**症状**：`Expected where to have exactly one operator, got {'owner': 'x', 'source': 'y'}`

**根因**：Chroma 的 `delete` / `get` 不接受平铺多键 dict 作为 where 条件。

**修复**：所有多键 where 改为 `{"$and": [{"owner": owner}, {"source": source}]}`。

### 5. 前端 reactive 对象不能用 .value

**症状**：浏览器中 SSE 连接正常（curl 验证通过），但前端 UI 永远停在"推理中"，控制台报 `Cannot read properties of undefined (reading 'content')`。

**根因**：`reactive({...})` 返回的是 Proxy 对象，直接用 `assistantMsg.value.content` 访问会返回 undefined，第一个 token 到达就抛 TypeError 中断 reader 循环。

**修复**：`reactive()` 创建的对象直接用 `assistantMsg.content += delta`，不用 `.value`。只有 `ref()` 才需要 `.value`。

### 6. 鉴权中间件双通道

中间件洋葱顺序（后 add 更靠外）：OTel → Session → Security → 路由。

Security 中间件判断逻辑：
- Cookie 登录用户：`session_user_id(request)` 返回 uid → `owner = f"user-{uid}"`
- Bearer API Key：`has_valid_api_key(request)` → `owner = None`（全局，不做隔离）
- 两者皆无：401

公开路径白名单：`/api/v1/auth/register`、`/api/v1/auth/login`。

### 7. RAG 按 owner 隔离

- **写入**：`ingest_document(filename, content, user_id)` 在 metadata 加 `"owner": user_id or "_global"`，落盘到 `/data/ingest/<owner>/`
- **检索**：`get_retriever(user_id)` 设置 `search_kwargs["filter"] = {"owner": user_id}`
- **统计/删除**：按 owner 过滤；API Key 调用 `user_id=None` → 全局视角可见全部

### 8. Docker 多阶段构建

```
Stage 1: node:22-alpine → npm install + npm run build → /fe/dist
Stage 2: python:3.14-slim + uv → 安装依赖到 .venv
Stage 3: runtime → COPY /fe/dist → /app/static, COPY .venv, chown agent
```

- `workers=1`（PostgresSaver 单连接 + SSE 不能多 worker）
- named volume `/data/ingest` 首次挂载可能不继承 Dockerfile chown，需要 `docker exec -u root chown -R agent:agent /data/ingest/`

### 9. embedding 400 "invalid input type"

OpenAIEmbeddings 必须设 `check_embedding_ctx_length=False`，否则部分模型/tokenizer 组合会 400。已修复，勿删。

### 10. PowerShell 执行策略

复杂内联 PowerShell 命令可能被 ExecutionPolicy Restricted 拦截。拆成单条简单命令或用脚本文件绕过。

## 验证清单

1. `curl http://localhost:18088/` → 返回 index.html（SPA 托管）
2. `curl /login`（history fallback）→ 200 返回 index.html
3. 注册 → 200；重复注册 → 409
4. 登录后 `/api/v1/auth/me` 带 cookie → 返回用户
5. SSE：`curl -N -b cookie /api/v1/ui/chat/stream -d '{"message":"..."}'`，应看到 `event: meta` → `event: token`（多次）→ `event: done`
6. 多轮上下文：同一 conversation_id 第二次消息引用上文
7. 用户隔离：B 用户列表空、读/删 A 会话 404、RAG 检索 A 的文档无结果
8. Bearer API Key：`/api/v1/agent` 仍可用，owner=None 全局
9. 刷新整页：登录态保持、会话和消息仍在
10. 浏览器实测打字机：长回答（>100字）应逐字增量出现，短回答瞬间完成
