# EdAgent 会话交接文档

> 写给下一个完全没有上下文的会话。生成时间：2026-10-13。
> 交接前已重新验证容器状态，信息属实。本轮新增 Team OS（多角色协作子系统）阶段 1-3。

## 1. 我们在做什么任务

在本机（Windows + Docker Desktop，项目路径 `c:\djangoproject\EdAgent`）从零构建一个**全本地化 AI Agent 平台 EdAgent**，要求：

- LangGraph 工作流 Agent（智能路由：简单问答 / 工具调用 / RAG 检索 / 多 Agent 协作）
- RAG 知识库（ChromaDB，按用户隔离）
- 自带 Web UI（Vue3 SPA）+ 用户注册/登录 + 会话历史持久化
- SSE 流式打字机输出（含思考过程展示 + 客户端中断取消）
- 全部 Docker 化，含可观测性（Prometheus / Grafana / Jaeger / OTel）
- LLM 用容器内 Ollama（deepseek-r1:8b + nomic-embed-text），可切换在线 API

**详细架构、API、Schema、部署与迁移指南全部在** **[PROJECT.md](PROJECT.md)，那是权威文档，先读它。**

## 2. 当前状态：已完成并全部验证通过

截至交接时（2026-10-13）用 `docker ps` 验证：**10 个 edagent 容器全部 Up，其中带健康检查的均为 healthy**（另有一组与本项目无关的 `dev_*` 容器）。

| 入口                              | 地址                        | 凭据                                                           |
| ------------------------------- | ------------------------- | ------------------------------------------------------------ |
| Web UI（主入口，Nginx）               | <http://localhost:18088/> | 管理员账号 `testuser` / `test123456`（admin 角色）                     |
| API 直连（FastAPI，含 /docs Swagger） | <http://localhost:18080>  | Bearer API Key `edagent-sk-7f3c9a21e8b44d60a5c28b17f6d309e2` |
| Grafana                         | <http://localhost:13000>  | `admin` / `Gr@f_5d8a2f3c71e9`                                |
| Jaeger                          | <http://localhost:16686>  | 无                                                            |
| Prometheus                      | <http://localhost:19090>  | 无                                                            |

已交付并验证过的功能：

1. 基础设施：postgres / redis / ollama / chromadb / agent-api / nginx / otel-collector / jaeger / prometheus / grafana，Docker 三阶段构建（Node 构建前端 → uv 装 Python 依赖 → runtime），CPU 模式启动命令：`docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d`
2. LangGraph Agent：router → direct\_answer / retrieve+agent(ReAct) / team（多 Agent 子图）/ report，`astream_events(v2)` SSE 逐 token 输出
3. 用户系统：注册/登录/登出/me，PBKDF2 密码哈希，Cookie Session（7 天），admin/user 角色（首个注册者自动成为 admin）
4. 管理端点：`/api/v1/admin/users` 列表 / 改角色 / 删除（防护：不能操作自己、至少保留一名管理员、删除级联清理检查点）
5. 会话持久化：conversations / messages 表，支持恢复、重命名、删除、**导出 Markdown**，URL `?c=<conversation_id>` 恢复
6. RAG 按用户隔离：上传 PDF/DOCX/XLSX/MD/TXT/HTML → 分块 → ChromaDB（xlsx 按工作表分页），metadata `owner` 过滤；Cookie 用户只看自己的，API Key 全局
7. SSE 流式取消：客户端断连（停止按钮/关页面）→ 取消图执行与 LLM 调用，已生成部分答案自动落库
8. 多 Agent 协作子图：planner → worker(ReAct) ↔ tools → reviewer（approve/revise，最多 2 轮），复杂分析题由路由选中 `team` 分支
9. 双通道鉴权：Cookie Session（UI 用户）+ Bearer API Key（脚本），公开白名单仅 register/login
10. 可观测性：Grafana 仪表盘自动供给，Jaeger 链路追踪正常
11. 项目内沉淀了 5 个 Trae Skill（`.trae/skills/`），其中 `edagent-user-ui-sse` 是用户系统/SSE 排错指南
12. 管理后台页面：前端 `/admin` 路由（Admin.vue），用户列表表格 + 角色切换 + 删除，路由守卫 `requiresAdmin`
13. 会话置顶/搜索：conversations.pinned 列（幂等迁移），`POST /conversations/{id}/pin` 切换置顶，`GET /conversations?q=` 标题模糊搜索，置顶优先排序
14. PPTX/CSV 摄取：pyproject.toml 新增 python-pptx + markdown-it-py；ingest.py 支持 .pptx（按幻灯片分页，含表格）和 .csv（标准库 csv.reader）
15. 导出格式扩展：`GET /conversations/{id}/export?format=md|html`；HTML 用 markdown-it-py 渲染 + 内联 CSS 自包含页面；PDF 走前端 `window.open` 打开 HTML 页面 → Ctrl+P 打印
16. 协作小组可配置：`team_max_rounds` / `team_planner_prompt` / `team_reviewer_prompt` 移入 Settings（AGENT_TEAM_ 前缀），multi_agent.py 改用 settings 取代硬编码常量

以上 16 项为「主线 Agent」功能；以下为 2026-10-13 新增的 **Team OS（多角色协作子系统，独立于主图）**：

17. **角色注册表**：6 个角色 `pm / product / architect / developer / tester / reviewer` 定义在 `app/team/roles/*.yaml`，服务启动时 `sync_roles_to_db()` 幂等 upsert 到 `agent_roles` 表。
18. **PM 拆解 + DAG 并发调度**：PM Agent 产出 WBS（任务含 `depends_on`）→ `app/team/scheduler.py` 拓扑排序 + `asyncio.Semaphore(team_max_parallel)` 并发执行；`requires_human_confirm` 的角色任务先置 `blocked`，用户确认后继续。
19. **PR / 补丁机制**：developer 角色输出代码块 → `app/team/patch.py` 的 `parse_code_blocks` + `generate_diff`（unified diff）→ 存 `team_prs` → UI 触发 `apply_diff`（`patch -p1`）。
20. **Docker 沙箱**：`app/team/sandbox.py` 用一次性容器（`--network=none` / 256MB / 0.5 CPU，镜像默认 `edagent-agent-api:0.1.0`）写入开发产物并跑 `python -m compileall` 验证；需 agent-api 挂载 `/var/run/docker.sock` 且 `group_add: ["0"]`。沙箱失败不阻塞 PR 生成（优雅降级）。
21. **Team OS 阶段 3**：多团队隔离（`teams` 表 + `/team/list|create|{id}/rename|DELETE`）、通知系统（`notifications` 表 + `/team/notifications*`，团队跑完按终态写通知）、团队模板库（`app/team/templates.py`：software_dev / content_creation / data_analysis）。

以下为 2026-10-15 新增的 **Phase 3 能力扩展**（三个功能均已端到端验证通过）：

22. **插件系统（进程内 Python 插件）**：`app/plugins/loader.py` 用 `importlib` 扫描宿主机 `plugins/*.py`（compose bind mount 到 `/app/plugins`），收集其中 `@tool` 函数合并进 `TOOLS`/`TOOL_MAP`，Agent 立即可调用。管理端点 `GET/POST/DELETE /api/v1/plugins`（仅 admin，上传限 .py、≤1MB）。示例插件 `plugins/example_plugin.py`（random_number）。
23. **批量文档导入**：`POST /api/v1/rag/batch`（≤20 文件，读取后 `asyncio.create_task` 后台执行 + `asyncio.Semaphore(2)` 限流）+ `GET /api/v1/rag/batch/{job_id}` 轮询；`ingest_jobs` 表记录逐文件状态（results JSONB）。前端 Chat.vue 知识库抽屉改为「先选文件 → 点开始导入 → 进度条 + 逐文件 tag」。
24. **RAG 检索调参**：Settings 新增 `rag_search_type`（mmr/similarity/similarity_score_threshold，非法值回退 mmr）、`rag_score_threshold`，配合已有 `rag_top_k`/`rag_mmr_lambda_mult` 在 `retriever.py` 组装 search_kwargs。默认行为与之前完全一致（mmr k=5 λ=0.5）。

以下为 2026-10-15 新增的**会话组织 + 实时协作**两个扩展（均已端到端验证通过）：

25. **会话标签分类（多维度组织会话）**：conversations 表新增 `tags JSONB` / `category` / `favorite` 列（幂等迁移写在 `app/db.py` 初始化 SQL，索引 `idx_conversations_share_token` 亦在此）。接口：`PATCH /api/v1/ui/conversations/{id}/meta` 局部更新（tags / category / favorite **只改请求体里显式出现的字段**，`tags: []` 清空标签、`category: ""` 清空分类）、`GET /api/v1/ui/conversations?tag=&category=&favorite=` 组合筛选（标签走 `tags @> %s::jsonb` 包含查询）、`GET /api/v1/ui/conversation-filters` 聚合分类/标签计数。前端 Chat.vue 侧栏新增「全部/收藏」单选 + 「按分类/按标签」下拉，会话行显示分类/标签徽标、收藏星标、分享标记，更多菜单含收藏、标签分类、分享、导出、删除。
26. **WebSocket 实时多用户协作（只读围观）**：`POST /conversations/{id}/share` 生成 `share_token` → 分享链接 `/?c=<id>&share=<token>`（任何人登录后凭链接只读围观）；`GET /api/v1/ui/shared/{token}` 读共享会话+消息；`WS /api/v1/ui/ws/{token}` 加入房间，事件协议 `history → presence/join/leave → turn_start → reasoning/token → turn_done/turn_error`；`DELETE .../share` 关闭分享并**强制解散房间**（`hub.close_room()` 推 error 事件 + close 4404）。广播用进程内单例 `app/api/broadcast.py::CollaborationHub`（uvicorn 单 worker 成立；若将来改多 worker 必须换成外部分发）。`chat/stream` 的 producer 每轮问答向房间广播 `turn_start` / `token` / `reasoning` / `turn_done`（中断时下发含部分答案的 turn_done，异常下发 turn_error）。在线成员**按用户 id 去重**（同一账号开多个标签页只算 1 人）。前端 Chat.vue 协作态：只读视图（无输入框，底部只读提示条）+ 顶部在线成员头像与「N 人在线」+ 实时打字机效果。

## 3. 当前卡在哪

**代码层面无未解决阻塞**，系统可用。

**Team OS 全链路端到端实跑已于 2026-10-15 闭环**（team acd51c3e）：scheduler 死循环修复生效、DAG 依赖调度、人工确认流（blocked→confirm→resume）、通知系统（task_blocked/task_failed×2/team_done 全触发）全部验证通过；product 角色成功产出产物。developer/tester 失败为环境资源限制（CPU 900s 推理超时 + Ollama 模型进程资源不足崩溃），非代码缺陷；要让他们也跑通需升级推理资源或调大超时/减小任务粒度。

注意 `POST /api/v1/team/start` 是**长阻塞请求**：`TeamRunner.start()` 内部 `await` 了 PM 拆解 + 整个 DAG 调度，直到「全部完成或无法推进（如有任务转为 blocked 等待人工确认）」才返回。观察进度请直接查库（team 记录在 PM 之前就已写入）：

```powershell
docker exec edagent-postgres psql -U agent -d ai_agent -c "SELECT role,status,title FROM team_tasks ORDER BY created_at"
```

需要知道的环境事实：

- **项目不是 git 仓库**（无 `.git`），改动前考虑备份，回滚只能靠手动副本。
- agent-api 镜像最新于 2026-10-15 重建（含 Phase 3：插件系统 + 批量导入 + RAG 调参，以及两个扩展：会话标签分类 + WebSocket 协作围观）；改了后端/前端代码后需要重建：`docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d --build agent-api`。**只改 .py 不重建的话容器里跑的还是旧代码**——曾因此误以为修复已生效，实际容器内仍是旧代码（用 `docker cp edagent-agent-api:/app/app/team/scheduler.py <tmp>` 拷出来核对可确认）。**另外：docker build 在启动时读取构建上下文，若在构建过程中又改了源码，那一轮构建不会包含新改动**（本轮实际踩到过一次，用 `docker exec edagent-agent-api grep <新代码特征> app/...` 核对后才补建）。

## 4. 下一步计划（候选，用户尚未指定优先级）

前两批共 10 个扩展（角色系统+管理端点、DOCX/XLSX 摄取、会话导出 Markdown、SSE 流式取消、多 Agent 协作子图；管理后台页面、会话置顶/搜索、PPTX/CSV 摄取、导出格式扩展、协作小组可配置）已于 2026-09-28 全部完成并验证。

**Team OS 阶段 1-3 已于 2026-10-13 实现，并完成接口级验证**（沙箱生命周期、多团队 CRUD、模板库、通知端点、6 张 Team OS 表均确认可用）；全链路实跑于 2026-10-15 闭环，见第 3 节。

**Phase 3 能力扩展（插件系统、批量导入、RAG 调参）已于 2026-10-15 实现并端到端验证通过**（插件 random_number 已进 TOOLS 且可调用；批量导入 2 文件 done 且逐文件状态正确；RAG 调参 mmr/threshold 模式均生效）。

**会话标签分类 + WebSocket 实时多用户协作（只读围观）两个扩展已于 2026-10-15 实现并端到端验证通过**（25 项接口/协议断言全通过 + 浏览器渲染核对通过），详见功能清单 25/26。

来自 PROJECT.md 第 18 节的剩余候选方向：

- **模型升级**：改 `.env` 的 `LLM_MODEL`（如 qwen2.5:14b），Ollama 自动拉取；r1:8b 在 CPU 上思考偏慢，升级收益大
- **多语言界面**：i18n 支持中英文切换
- （已完成：「会话标签分类」「WebSocket 实时多用户协作」见功能 25/26）

**注意：先问用户想做什么，不要自作主张选一个开始。**

## 5. 踩过的坑——绝对不要再踩

### 后端 / LangGraph（修复已固化在代码里，勿回退）

1. **必须用** **`AsyncPostgresSaver`**（`app/memory/checkpointer.py`），同步 `PostgresSaver` 会让 SSE 报 `NotImplementedError`。所有驱动图的节点必须 `async def` + `ainvoke`，否则 token 级事件不冒泡。
2. **`OpenAIEmbeddings`** **必须保留** **`check_embedding_ctx_length=False`**，否则 embedding 400 "invalid input type"。已修复，勿删。
3. **anyio** **`to_thread.run_sync`** **不透传 kwargs**，要用 `functools.partial` 包装（否则 `TypeError: unexpected keyword argument`）。
4. **Chroma 多键 where 必须用** **`{"$and": [...]}`**，平铺 dict 报 "exactly one operator"。
5. **uvicorn 必须** **`workers=1`**（AsyncPostgresSaver 单连接 + SSE 长连接不兼容多进程），勿改多 worker。
6. **SSE 事件只转发** **`on_chat_model_stream`** 且过滤 `langgraph_node ∈ {direct_answer, agent, worker}`（排除 router 内部 JSON、子图 planner/reviewer 内部消息），协议为 meta → reasoning/token → done/error。
7. **deepseek-r1:8b 在 CPU 上长思考可达 5 分钟+，期间 0 token / 0 reasoning 事件是正常现象**（r1 的思考增量不走 `reasoning_content` 流式字段），别误判流式坏了。排查顺序：查 checkpoint 确认 router 是否完成 → 换简单问题对照 → 看 ollama 日志。
8. **子图 token 冒泡**：包装节点内 `await subgraph.ainvoke(state, config=config)` 必须透传 config（保 user_id 隔离 + 事件冒泡）；子图内节点名要加入 `_STREAM_NODES`；子图状态用独立 TypedDict（TeamState），包装节点只回填主图 schema 有的 key。
9. **SSE 中断取消**：`chat/stream` 用「producer 任务 + asyncio.Queue + 每秒轮询 `request.is_disconnected()`」结构；断连时 cancel producer（连带取消 LLM 调用），并在 finally 里把部分答案同步落库（清理阶段别用 to_thread）。
10. **markdown-it-py 的 MarkdownIt 构造函数不接受 `html=True` 等直接参数**，正确写法：`MarkdownIt("commonmark", {"html": True}).enable("table")`。直接传关键字参数会报 `TypeError: unexpected keyword argument 'html'`。
11. **Pydantic Settings 的字符串字段中不能包含中文全角引号**（如 \u201c \u201d），会被解释器视为字符串终止符导致 `SyntaxError: invalid syntax`。用单引号包裹含中文引号的字符串可修复。
12. **DAG 调度器 `scheduler.py` 的 while 循环每个分支都必须有 `await` 或立即 `break`**。`elif pending and not ready:` 分支若既不 break 也无 await，会空转卡死整个事件循环——表现为 `/team/start` 永不返回、整个 agent-api 挂死（`/health` 超时 000），但 Ollama 日志里 LLM 调用其实成功了。修复（已固化，勿回退）：该分支统一 `break`（有 blocked 任务则等用户 confirm 后由 `confirm_task` 重新调度；无 blocked 则记循环依赖告警）。
13. **`@tool` 装饰后的函数是 `StructuredTool` 实例（Pydantic 模型），`callable()` 返回 `False`**。插件加载器检测工具时**只能查 `hasattr(attr, "name") and hasattr(attr, "invoke")`，不能加 `callable()` 判断**，否则插件会被静默跳过（加载结果为空列表）。此坑在 Phase 3 插件系统实踩过一次。
14. **WebSocket 端点必须先 `await websocket.accept()` 再校验身份/资源**。未 accept 就 `close()` 时 FastAPI 会返回 HTTP 403，浏览器只能拿到 `onclose code=1006`，无法携带语义关闭码；先 accept，再 `send_json({"type": "error", ...})` + `close(code=4401/4404)`，前端才能据 44xx 给出「登录已失效 / 分享链接已关闭」的明确提示。因此 **`session_user_id()` 形参类型放宽为 `HTTPConnection`**（`Request` 与 `WebSocket` 的共同基类，SessionMiddleware 同时作用于 http 与 websocket scope；注意 `BaseHTTPMiddleware` 只拦 http，WS 鉴权只能在端点里自己做）。
15. **协作广播只需进程内单例**：uvicorn `--workers 1` 下 `app/api/broadcast.py::CollaborationHub` 的字典就是全部在线连接，无需 Redis pub/sub；**一旦改多 worker 就必须换外部分发**（会静默失效：每个 worker 只知道自己房间里的连接）。

### 调试工具链（容器内）

1. 容器内跑临时脚本：`docker exec -e PYTHONPATH=/app edagent-agent-api /app/.venv/bin/python /tmp/xxx.py`——系统 python 没有依赖，且脚本放 /tmp 时 sys.path 不含 cwd。
2. 查 LangGraph 检查点状态：`state.checkpoint["channel_values"]`，CheckpointTuple 上没有 `.values` 属性。

### 前端

1. **`reactive()`** **对象不能** **`.value`**：`assistantMsg.content += delta`，只有 `ref()` 才用 `.value`。误用会导致 UI 永远停在"推理中"。
2. **@element-plus/icons-vue 没有** **`Calculator`** **图标**，Rollup 构建会报 "not exported"，用 `DataAnalysis`。

### 部署 / 运维

1. **named volume** **`/data/ingest`** **首次挂载可能不继承 Dockerfile 的 chown**，上传文档报权限错误时执行：`docker exec -u root edagent-agent-api chown -R agent:agent /data/ingest/`
2. **ollama 官方镜像曾有 llama-server 缺失问题**，当前镜像是修复过的（重建版）。若重建 ollama 容器后 LLM 挂了，先怀疑镜像，别急着重装模型。
3. **浏览器访问 Grafana/Swagger 报 404 或按钮缺失，先怀疑浏览器缓存/Service Worker 残留**（之前 13000 端口跑过 Vite dev server 留下了 SW）。解决：无痕窗口或 Ctrl+F5 硬刷新，别去改服务端。
4. **Swagger 的 Authorize 按钮依赖 main.py 里的 Bearer 认证声明**，已加，勿删。
5. **Debian 的 `docker.io` 包不含 docker CLI**（只有 dockerd/docker-proxy/docker-init）；CLI 在独立的 **`docker-cli`** 包里（装到 `/usr/bin/docker`）。沙箱机制依赖容器内的 docker CLI。
6. **Docker Desktop 下 `/var/run/docker.sock` 属 root:root 0660**，非 root 的 agent 用户默认无权访问 → compose 的 agent-api 必须加 `group_add: ["0"]` 才能调 docker。
7. **Dockerfile 首行 `# syntax=docker/dockerfile:1.7` 会强制从镜像源拉取 `docker/dockerfile` 前端镜像**，国内镜像源（`docker.m.daocloud.io`）抖动时返回 502 导致构建直接失败 → 已删除该行，改用 BuildKit 内建前端（同样支持 `RUN --mount=type=cache`）。
8. **沙箱里 `/workspace` 由容器运行时以 root 创建**，而镜像默认用户是 agent，写 `__pycache__` 会 `PermissionError` → `sandbox.py` 在写入文件前先 `docker exec -u root ... sh -c "mkdir -p /workspace && chmod 777 /workspace"`。
9. **补丁机制依赖容器内的 `diff` / `patch`**（Dockerfile 已装 `diffutils patch`）；缺了会导致 PR apply 报 `FileNotFoundError`。
10. **Windows 笔记本休眠/隔天后 Docker Desktop 引擎 API 会整体卡死**：所有 `docker` 命令报 `Internal Server Error ... dockerDesktopLinuxEngine`，但 `Get-Process` 看进程还在。只能重启 Docker Desktop（杀掉 `Docker Desktop` + `com.docker.backend` 进程再启动 `C:\Program Files\Docker\Docker\Docker Desktop.exe`）；容器按 `restart: unless-stopped` 自动恢复，数据在 named volume 不丢。**注意：从受限沙箱 shell 里 `Start-Process` 启动 Docker Desktop 会被拦截 AppData 写入导致后端起不来**（进程起来几秒就消失），需用非沙箱模式启动。另外重启会打断进行中的团队运行，被中断的任务停在 `running` 成孤儿——重新调用一次该任务的 `/confirm` 接口即可让调度器重新捡起（run_team 会把 `running` 当待执行重新调度）。
11. **Nginx 反代 WebSocket 必须显式转发 Upgrade 头**：`config/nginx.conf` 的 http 块加了 `map $http_upgrade $connection_upgrade { default upgrade; '' ""; }`，并在 `location /` **之前**新增 `location /api/v1/ui/ws/`（`proxy_http_version 1.1` + `Upgrade $http_upgrade` + `Connection $connection_upgrade` + `proxy_buffering off` + `proxy_read_timeout/send_timeout 3600s`）。原 `location /` 里是 `Connection ""`（为 SSE 关闭分块缓冲用），不覆盖会退化成普通 HTTP 导致 WS 握手失败。nginx 配置是 `./config/nginx.conf:ro` 挂载的，改完 `docker compose ... up -d nginx` 即可生效（无需重建镜像）。
12. **验证前端产物是否包含新代码**：构建产物在 agent-api 容器内 `/app/static`（不是 nginx 容器），例如 `docker exec edagent-agent-api grep -c '协作围观' /app/static/assets/*.js`。另外**在容器内跑 HTTP/WS 测试脚本比在本机 PowerShell 里写 curl 稳得多**（无引号/ExecutionPolicy 问题）：把脚本 `docker cp` 进容器后 `docker exec edagent-agent-api python /tmp/xxx.py`（httpx + websockets 都已随 uvicorn[standard] 装好，容器内后端地址是 `http://127.0.0.1:8080`）。

### 本机工具链（2026-09-28 交接时再次验证）

1. **PowerShell 5 不支持** **`&&`** **链接命令**，用 `;` 或分开执行。
2. **复杂内联 PowerShell 会被 ExecutionPolicy Restricted 拦截**，拆成单条简单命令或写成脚本文件。
3. **本机 shell 里** **`git`** **不在 PATH**，且项目本来就没有 .git——别浪费时间试 git 命令。

## 6. 新会话快速上手

```powershell
# 看状态（10 个容器应全部 Up/healthy）
docker compose ps

# 常用命令（完整列表见 PROJECT.md 第 15 节）
docker logs edagent-agent-api --tail 50 -f          # 后端日志
docker exec -it edagent-postgres psql -U agent -d ai_agent   # 查库

# 修改后端/前端代码后重建
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d --build agent-api

# SSE 冒烟测试（先登录拿 cookie）
curl -N -b cookie.txt http://localhost:18088/api/v1/ui/chat/stream -d '{"message":"你好"}' -H "Content-Type: application/json"
```

关键文件速查：

| 文件                                                                                     | 作用                                                      |
| -------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| [PROJECT.md](PROJECT.md)                                                               | **权威文档**：架构/API/Schema/部署/迁移/坑点全在这                      |
| [.env](.env) / [.env.example](.env.example)                                            | 端口、密码、LLM 配置（`AGENT_` 前缀映射到 `app/config.py` 的 Settings） |
| [app/main.py](app/main.py)                                                             | FastAPI 入口（lifespan/中间件/路由/SPA 托管）                      |
| [app/agents/graph.py](app/agents/graph.py)                                             | LangGraph 主图组装                                          |
| [app/agents/multi\_agent.py](app/agents/multi_agent.py)                                | 多 Agent 协作子图（planner→worker→reviewer）                   |
| [app/api/ui\_routes.py](app/api/ui_routes.py)                                          | SSE 流式聊天 + 会话管理 + 导出端点                                  |
| [app/api/admin\_routes.py](app/api/admin_routes.py)                                    | 管理端点（用户列表/改角色/删除）                                       |
| [frontend/src/views/Chat.vue](frontend/src/views/Chat.vue)                             | 聊天主页面                                                   |
| [frontend/src/views/Admin.vue](frontend/src/views/Admin.vue)                           | 管理后台页面（用户列表/角色切换/删除）                                |
| [app/team/](app/team/)                                                                 | **Team OS 子系统**：roles / nodes / patch / sandbox / scheduler / templates / runner |
| [app/api/team\_routes.py](app/api/team_routes.py)                                      | Team OS API（start / list / create / rename / delete / artifacts / prs / notifications / templates） |
| [frontend/src/views/Team.vue](frontend/src/views/Team.vue)                             | 团队协作页面（模板选择 / 我的团队 / 通知铃铛）                              |
| [.trae/skills/edagent-user-ui-sse/SKILL.md](.trae/skills/edagent-user-ui-sse/SKILL.md) | 用户系统+SSE 的 10 个坑点详解与验证清单                                |

数据卷（7 个，迁移指南见 PROJECT.md 14.4-14.5）：`edagent_pg_data`、`edagent_chroma_data`、`edagent_ollama_data`（约 5GB 模型权重，丢了要重新拉）为必须保留；`edagent_ingest_data` 推荐保留。
