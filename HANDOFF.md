# EdAgent 会话交接文档

> 写给下一个完全没有上下文的会话。生成时间：2026-09-28。
> 交接前已重新验证容器状态，信息属实。

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

截至交接时（2026-09-28）刚刚用 `docker compose ps` 重新验证：**10 个容器全部 Up 5 days，其中带健康检查的均为 healthy**。

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

## 3. 当前卡在哪

**没有未解决的阻塞。** 所有已知问题均已修复，系统处于完全可用状态。

唯一需要知道的环境事实：

- **项目不是 git 仓库**（无 `.git`），没有版本控制。改动前考虑备份，回滚只能靠 `traebackup/` 目录或手动副本。
- 容器已连续运行 5 天，如果改了后端/前端代码需要重建：`docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d --build agent-api`

## 4. 下一步计划（候选，用户尚未指定优先级）

前两批共 10 个扩展（角色系统+管理端点、DOCX/XLSX 摄取、会话导出 Markdown、SSE 流式取消、多 Agent 协作子图；管理后台页面、会话置顶/搜索、PPTX/CSV 摄取、导出格式扩展、协作小组可配置）已于 2026-09-28 全部完成并验证。

来自 PROJECT.md 第 18 节的新一批候选方向：

- **模型升级**：改 `.env` 的 `LLM_MODEL`（如 qwen2.5:14b），Ollama 自动拉取；r1:8b 在 CPU 上思考偏慢，升级收益大
- **会话标签分类**：多维度组织会话（标签 / 分类 / 收藏夹）
- **批量文档导入**：文件夹上传，一次性摄取整个目录
- **WebSocket 实时多用户协作**：多用户实时共享会话视图
- **插件系统**：自定义工具扩展机制
- **多语言界面**：i18n 支持中英文切换

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
| [.trae/skills/edagent-user-ui-sse/SKILL.md](.trae/skills/edagent-user-ui-sse/SKILL.md) | 用户系统+SSE 的 10 个坑点详解与验证清单                                |

数据卷（7 个，迁移指南见 PROJECT.md 14.4-14.5）：`edagent_pg_data`、`edagent_chroma_data`、`edagent_ollama_data`（约 5GB 模型权重，丢了要重新拉）为必须保留；`edagent_ingest_data` 推荐保留。
