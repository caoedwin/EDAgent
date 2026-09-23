# EdAgent 项目文档

> 本地化 AI Agent 平台：LangGraph 工作流 + RAG 知识库 + Web UI + 用户系统，全部 Docker 化运行。

## 1. 项目概述

EdAgent 是一个基于 LangGraph 的本地 AI Agent，支持：

- **智能路由**：自动判断简单问答 / 工具调用 / RAG 检索
- **流式输出**：SSE 逐 token 打字机效果 + 思考过程展示
- **用户系统**：注册 / 登录 / 会话历史持久化（Cookie Session）
- **RAG 知识库**：按用户隔离的文档上传与检索（ChromaDB）
- **可观测性**：Prometheus 指标 + OpenTelemetry/Jaeger 链路追踪
- **双通道鉴权**：Web UI 走 Cookie Session，API 脚本走 Bearer API Key

LLM 默认使用容器内 Ollama（deepseek-r1:8b + nomic-embed-text），可无缝切换到在线 API（DeepSeek/OpenAI）。

## 2. 架构总览

```
                        ┌─────────────┐
                        │   Nginx     │  http://localhost:18088
                        │  (反向代理)  │
                        └──────┬──────┘
                               │
                        ┌──────▼──────┐
                        │  agent-api  │  FastAPI (uvicorn workers=1)
                        │  Python 3.14│  http://localhost:18080
                        └──┬──┬──┬──┬─┘
           ┌────────────────┘  │  │  └────────────────┐
    ┌──────▼──────┐  ┌────────▼──▼──┐  ┌──────────────▼──────┐
    │   Ollama    │  │  PostgreSQL   │  │     ChromaDB        │
    │ LLM+Embed   │  │ 业务表+检查点  │  │  向量库(RAG)        │
    │ :11434      │  │ :5432         │  │  :8000              │
    └─────────────┘  └───────────────┘  └─────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │  Redis (缓存)       │  :6379
                    └─────────────────────┘

    可观测性：OTel Collector → Jaeger (UI :16686) | Prometheus → Grafana (UI :13000)
```

## 3. 目录结构

```
EdAgent/
├── app/                          # 后端 Python 应用
│   ├── main.py                   #    FastAPI 入口（lifespan/中间件/路由/SPA 托管）
│   ├── config.py                 #    统一配置（Settings 类，AGENT_ 前缀环境变量）
│   ├── db.py                     #    业务数据库（pg 连接池 + 建表 + CRUD）
│   ├── api/
│   │   ├── routes.py             #    API 路由（/chat /agent /rag/*）
│   │   ├── auth_routes.py        #    认证路由（/auth/register /login /logout /me）
│   │   ├── ui_routes.py          #    UI 路由（/ui/conversations /ui/chat/stream SSE）
│   │   └── schemas.py            #    Pydantic 请求/响应模型
│   ├── agents/
│   │   ├── graph.py              #    LangGraph 图组装（router→answer/rag/react→report）
│   │   ├── state.py              #    Agent 状态定义（TypedDict）
│   │   ├── nodes.py              #    各节点实现（async：router/direct/agent/tools/report）
│   │   └── tools.py             #    Agent 工具（knowledge_search 等）
│   ├── llm/
│   │   └── client.py             #    LLM 客户端（OpenAI 兼容协议，chat_invoke/ainvoke）
│   ├── rag/
│   │   ├── ingest.py             #    文档摄取（PDF/MD/TXT/HTML→分块→ChromaDB，按 owner 隔离）
│   │   └── retriever.py          #    MMR 检索（按 user_id 过滤 owner）
│   ├── security/
│   │   ├── auth.py               #    API Key + 密码校验基础
│   │   ├── web_auth.py           #    Session 鉴权（request_owner_id / require_login）
│   │   └── passwords.py          #    PBKDF2-HMAC-SHA256 密码哈希
│   ├── memory/
│   │   └── checkpointer.py       #    AsyncPostgresSaver 检查点（图状态持久化）
│   └── observability/
│       ├── metrics.py            #    Prometheus 指标中间件
│       └── tracing.py            #    OpenTelemetry 自动埋点
├── frontend/                     # 前端 Vue3 SPA
│   ├── package.json              #    依赖（Vue3.5 / Element Plus 2.8 / Pinia / marked）
│   ├── vite.config.js            #    Vite 配置（dev proxy /api → :18080）
│   ├── index.html                #    HTML 入口
│   ├── public/favicon.svg
│   └── src/
│       ├── main.js              #    应用入口（Element Plus + zhCn + 图标注册）
│       ├── App.vue              #    根组件
│       ├── styles.css           #    全局样式
│       ├── api/client.js        #    fetch 封装 + SSE 流式解析（streamChat）
│       ├── stores/auth.js       #    Pinia 认证 store
│       ├── router/index.js      #    Vue Router（/login /register / + 守卫）
│       └── views/
│           ├── Login.vue        #    登录页
│           ├── Register.vue     #    注册页
│           └── Chat.vue         #    聊天主页面（会话侧栏/SSE/Markdown/知识库抽屉）
├── config/                       # 基础设施配置
│   ├── nginx.conf                #    Nginx 反向代理（SSE 关闭缓冲）
│   ├── otel-collector.yaml       #    OTel Collector 管道配置
│   ├── prometheus.yml            #    Prometheus 采集配置
│   └── grafana/                  #    Grafana 仪表盘 + 数据源自动供给
│       ├── dashboards/edagent-overview.json
│       └── provisioning/
├── scripts/                      # 启动辅助脚本
│   ├── up.ps1 / up.sh            #    Docker Compose 启动
│   └── detect-gpu.ps1 / .sh     #    GPU 检测（选择 GPU/CPU compose 文件）
├── secrets/                      # Docker secrets
│   └── pg_password.txt           #    PostgreSQL 密码文件
├── .trae/skills/                 # 项目级 Trae Skills
│   ├── langgraph-agent-builder/ #    LangGraph 图构建指南
│   ├── rag-pipeline-builder/     #    RAG 管道构建指南
│   ├── observability-setup/      #    可观测性配置指南
│   ├── docker-compose-generator/ #    Docker Compose 生成指南
│   └── edagent-user-ui-sse/      #    用户系统+SSE+UI 排错指南
├── docker-compose.yml            # 主编排文件
├── docker-compose.cpu.yml        # CPU 模式覆盖
├── docker-compose.gpu.yml        # GPU 模式覆盖
├── Dockerfile                    # 三阶段构建（Node→Python→Runtime）
├── pyproject.toml                # Python 依赖
├── .env / .env.example           # 环境变量
└── PROJECT.md                    # 本文件
```

## 4. 技术栈

| 层 | 技术 | 版本 |
|---|---|---|
| 后端框架 | FastAPI + uvicorn | ≥0.115 / workers=1 |
| Agent 框架 | LangGraph + LangChain | ≥0.2 / ≥0.3 |
| LLM 协议 | OpenAI 兼容（Ollama / DeepSeek / OpenAI） | — |
| LLM（默认） | deepseek-r1:8b | Ollama |
| Embedding | nomic-embed-text | Ollama |
| 数据库 | PostgreSQL 16 | psycopg3 连接池 |
| 向量库 | ChromaDB | ≥0.5 |
| 缓存 | Redis 7.2 | — |
| 前端框架 | Vue 3.5 + Vite 5 | SPA |
| UI 库 | Element Plus 2.8 | 中文 zhCn |
| 状态管理 | Pinia 2 | — |
| 路由 | Vue Router 4 | history 模式 |
| Markdown | marked + DOMPurify | — |
| 鉴权 | Cookie Session（itsdangerous）+ Bearer API Key | — |
| 密码哈希 | PBKDF2-HMAC-SHA256 | 标准库 |
| 容器 | Docker / Docker Compose | — |
| 可观测性 | Prometheus + OTel + Jaeger + Grafana | — |

## 5. 配置详解

### 5.1 环境变量（.env）

复制 `.env.example` 为 `.env` 并修改密码：

```bash
cp .env.example .env
```

| 变量 | 默认值 | 说明 |
|---|---|---|
| **端口映射** | | |
| `OLLAMA_HOST_PORT` | 11534 | Ollama 宿主机端口 |
| `CHROMA_HOST_PORT` | 18000 | ChromaDB 宿主机端口 |
| `POSTGRES_HOST_PORT` | 15432 | PostgreSQL 宿主机端口 |
| `REDIS_HOST_PORT` | 16379 | Redis 宿主机端口 |
| `AGENT_API_PORT` | 18080 | agent-api 直连端口 |
| `NGINX_HOST_PORT` | 18088 | **Web 入口** |
| `OTEL_GRPC_HOST_PORT` | 14317 | OTel gRPC |
| `OTEL_HTTP_HOST_PORT` | 14318 | OTel HTTP |
| `JAEGER_UI_HOST_PORT` | 16686 | Jaeger UI |
| `PROMETHEUS_HOST_PORT` | 19090 | Prometheus |
| `GRAFANA_HOST_PORT` | 13000 | Grafana |
| **密码/密钥** | | |
| `PG_PASSWORD` | change_me | PostgreSQL 密码 |
| `AGENT_API_KEY` | change_me | Bearer API Key |
| `AGENT_SESSION_SECRET` | change_me | Cookie 签名密钥 |
| `GRAFANA_PASSWORD` | change_me | Grafana 管理员密码 |
| **LLM** | | |
| `LLM_BASE_URL` | http://ollama:11434/v1 | LLM API 地址 |
| `LLM_API_KEY` | ollama | LLM API Key |
| `LLM_MODEL` | deepseek-r1:8b | 模型名 |
| **Embedding** | | |
| `EMBEDDING_BASE_URL` | http://ollama:11434/v1 | Embedding API 地址 |
| `EMBEDDING_API_KEY` | ollama | Embedding API Key |
| `EMBEDDING_MODEL` | nomic-embed-text | 嵌入模型 |
| **CPU** | | |
| `OLLAMA_NUM_THREADS` | 8 | Ollama CPU 线程数 |

### 5.2 Settings 类（app/config.py）

所有 `AGENT_` 前缀的环境变量自动映射到 `Settings` 类：

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENT_", env_file=".env")

    # LLM
    llm_base_url / llm_api_key / llm_model / llm_temperature / llm_max_tokens / llm_timeout / llm_max_retries
    # Embedding
    embedding_base_url / embedding_api_key / embedding_model
    # 存储
    database_url / redis_url / chroma_url / collection_name / ingest_dir
    # Agent
    max_agent_steps: 15
    # RAG
    rag_chunk_size: 512 / rag_chunk_overlap: 50 / rag_top_k: 5 / rag_mmr_lambda_mult: 0.5
    # 可观测性
    otel_exporter_otlp_endpoint / otel_service_name
    # 安全
    api_key / session_secret / session_max_age_seconds (7天) / static_dir
```

### 5.3 Secrets

PostgreSQL 密码通过 Docker secrets 注入（`secrets/pg_password.txt`），不写在环境变量明文中。

## 6. Docker 服务

### 6.1 服务清单

| 服务 | 镜像 | 容器名 | 端口 | 依赖 |
|---|---|---|---|---|
| ollama | ollama/ollama:0.32.3 | edagent-ollama | 11534 | — |
| chromadb | chromadb/chroma:1.5.9 | edagent-chromadb | 18000 | — |
| postgres | postgres:16-alpine | edagent-postgres | 15432 | — |
| redis | redis:7.2-alpine | edagent-redis | 16379 | — |
| agent-api | 本地构建 | edagent-agent-api | 18080 | ollama+chroma+pg+redis |
| nginx | nginx:1.27-alpine | edagent-nginx | 18088 | agent-api |
| otel-collector | otel/opentelemetry-collector-contrib | edagent-otel-collector | 14317/14318 | jaeger |
| jaeger | jaegertracing/jaeger:2.6.0 | edagent-jaeger | 16686 | — |
| prometheus | prom/prometheus:v2.54.1 | edagent-prometheus | 19090 | — |
| grafana | grafana/grafana-oss:11.3.0 | edagent-grafana | 13000 | prometheus |

### 6.2 Dockerfile 三阶段构建

```
阶段 1 (frontend): node:22-alpine → npm install + npm run build → /fe/dist
阶段 2 (builder):   python:3.14-slim + uv → 安装依赖到 /app/.venv
阶段 3 (runtime):   python:3.14-slim → COPY .venv + app + static → uvicorn workers=1
```

- 前端 npm 走 npmmirror（国内加速）
- uv 从 PyPI 安装（不依赖 ghcr.io）
- `workers=1`：AsyncPostgresSaver 单连接 + SSE 长连接不兼容多进程

### 6.3 GPU / CPU 模式

```bash
# CPU 模式（默认）
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d

# GPU 模式（需要 NVIDIA GPU + nvidia-container-toolkit）
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

CPU 覆盖文件设置 `OLLAMA_NUM_THREADS`、`OLLAMA_NUM_PARALLEL` 等参数。

## 7. 数据库 Schema

复用同一个 PostgreSQL 实例，业务表与 LangGraph checkpoints 表并存。

### 7.1 业务表（app/db.py）

```sql
-- 用户
CREATE TABLE users (
    id            BIGSERIAL PRIMARY KEY,
    username      VARCHAR(32) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,           -- pbkdf2_sha256$240000$salt$hash
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- 会话
CREATE TABLE conversations (
    id         UUID PRIMARY KEY,
    user_id    BIGINT REFERENCES users(id) ON DELETE CASCADE,
    title      VARCHAR(100),
    thread_id  VARCHAR(80) UNIQUE,          -- 对应 LangGraph thread_id
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- 消息
CREATE TABLE messages (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
    role            VARCHAR(16),            -- user / assistant
    content         TEXT,
    reasoning       TEXT,                   -- 思考过程
    route           VARCHAR(16),           -- simple / rag / tool
    sources         JSONB,                 -- RAG 来源
    tool_results    JSONB,                 -- 工具调用结果
    created_at      TIMESTAMPTZ DEFAULT now()
);
```

### 7.2 LangGraph 检查点表

由 `AsyncPostgresSaver.setup()` 自动创建（`checkpoints`、`writes`、`migrations`），幂等执行。

## 8. Agent 图结构

```
START → router
         ├── simple → direct_answer → report → END
         ├── rag    → retrieve → agent ↔ tools → report → END
         └── tool   → agent ↔ tools → report → END
```

| 节点 | 职责 | 异步 |
|---|---|---|
| `router` | 判断任务类型（简单/工具/RAG） | async + ainvoke |
| `direct_answer` | 直接 LLM 回答 | async + ainvoke |
| `retrieve` | ChromaDB 语义检索（按 user_id 过滤） | async |
| `agent` | ReAct 循环（思考→行动→观察） | async + ainvoke |
| `tools` | 工具执行（knowledge_search 等） | sync |
| `report` | 结构化输出（answer/route/sources/tool_results） | sync |

**关键**：router/direct_answer/agent 三节点必须 `async def` + `ainvoke`，否则 `astream_events` 的 token 级事件不会冒泡。

## 9. API 端点

### 9.1 认证（/api/v1/auth）

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| POST | /register | 公开 | 注册（用户名3-32字符，密码≥6） |
| POST | /login | 公开 | 登录（写 Session Cookie） |
| POST | /logout | 登录 | 登出（清除 Session） |
| GET | /me | 登录 | 获取当前用户 |

### 9.2 UI 专用（/api/v1/ui）

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | /conversations | 登录 | 会话列表（仅本人） |
| GET | /conversations/{id} | 登录 | 会话历史消息 |
| DELETE | /conversations/{id} | 登录 | 删除会话（隔离校验） |
| PATCH | /conversations/{id} | 登录 | 重命名会话 |
| POST | /chat/stream | 登录 | **SSE 流式聊天** |

### 9.3 API / Agent（/api/v1）

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| POST | /chat | API Key | 简单问答 |
| POST | /agent | API Key | LangGraph Agent（非流式） |
| POST | /rag/documents | 双通道 | 上传文档（Cookie→用户隔离，API Key→全局） |
| DELETE | /rag/documents/{source} | 双通道 | 删除文档 |
| GET | /rag/stats | 双通道 | 知识库统计 |
| POST | /rag/query | 双通道 | RAG 检索/问答 |
| GET | /health | 公开 | 健康检查 |
| GET | /metrics | 公开 | Prometheus 指标 |

### 9.4 SSE 事件协议

| 事件 | 数据 | 说明 |
|---|---|---|
| `meta` | `{conversation_id, title}` | 会话元数据 |
| `reasoning` | `{delta}` | 思考过程增量 |
| `token` | `{delta}` | 回答 token 增量 |
| `done` | `{answer, route, sources, tool_results, model}` | 结构化最终结果 |
| `error` | `{message}` | 错误 |

## 10. 鉴权系统

### 10.1 双通道设计

| 通道 | 认证方式 | owner_id | RAG 隔离 | 适用场景 |
|---|---|---|---|---|
| Cookie Session | `edagent_session` Cookie | `user-{uid}` | 仅本人文档 | Web UI 用户 |
| Bearer API Key | `Authorization: Bearer` | `None`（全局） | 全部文档 | 脚本/自动化 |

### 10.2 中间件顺序

洋葱模型（后 add 更靠外）：OTel → Session → Security → 路由

- Security 中间件仅拦截 `/api/` 开头且不在公开白名单 `{/auth/register, /auth/login}` 的请求
- Session 中间件（itsdangerous）签名 Cookie，`same_site=lax`，`https_only=False`（本地 HTTP）

## 11. RAG 系统

### 11.1 文档摄取

- 支持格式：PDF / Markdown / TXT / HTML
- 分块：`rag_chunk_size=512`，`rag_chunk_overlap=50`
- 元数据：`{owner: "user-{uid}", source: "{hash}_{filename}", original_name, page}`
- 落盘：`/data/ingest/<owner>/<hash>_<filename>`

### 11.2 检索隔离

- `get_retriever(user_id)` → `search_kwargs["filter"] = {"owner": user_id}`
- Cookie 用户：`user_id = f"user-{uid}"`，只检索自己的文档
- API Key 调用：`user_id = None`，不做过滤（全局视角）

### 11.3 ChromaDB 多键 filter

Chroma 的 `delete` / `get` 不接受平铺多键 dict，需用 `$and`：
```python
where={"$and": [{"owner": owner}, {"source": source}]}
```

## 12. 前端

### 12.1 构建

前端在 Docker `node:22-alpine` 阶段构建，产物 `/fe/dist` 复制到 `/app/static`，由 FastAPI 同源托管。

### 12.2 路由

| 路径 | 组件 | 守卫 |
|---|---|---|
| /login | Login.vue | guestOnly |
| /register | Register.vue | guestOnly |
| / | Chat.vue | requiresAuth |

URL query `?c=<conversation_id>` 用于会话恢复。

### 12.3 SSE 解析

`streamChat()` 使用 `fetch + ReadableStream` 手动解析 SSE（非 EventSource，支持 POST）：
- 按 `\n\n` 分割事件块
- 解析 `event:` 和 `data:` 行
- JSON.parse payload，分派到 onMeta/onReasoning/onToken/onDone/onError

### 12.4 前端开发

```bash
cd frontend
npm install   # 需 Node 18+
npm run dev  # Vite dev server，proxy /api → localhost:18080
```

> Windows 本地 Node 仅为 v16 时，前端只在 Docker 内构建，不依赖本地 node。

## 13. 可观测性

| 组件 | 地址 | 用途 |
|---|---|---|
| Prometheus | http://localhost:19090 | 指标采集 |
| Grafana | http://localhost:13000 | 仪表盘（admin / {GRAFANA_PASSWORD}） |
| Jaeger | http://localhost:16686 | 链路追踪 |
| OTel Collector | :14317(gRPC) / :14318(HTTP) | 管道转发 |

Grafana 仪表盘自动供给（`config/grafana/provisioning/`），无需手动导入。

## 14. 部署指南（全新机器）

### 14.1 前置条件

- Docker Desktop（Windows）或 Docker Engine + Docker Compose v2（Linux）
- Windows: WSL2 后端
- 内存 ≥ 16GB（deepseek-r1:8b 约 6GB）
- 磁盘 ≥ 20GB（模型+数据）

### 14.2 步骤

```bash
# 1. 克隆项目
git clone <repo-url> EdAgent && cd EdAgent

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，修改所有 change_me 密码/密钥
# 生成 session secret: python -c "import secrets;print(secrets.token_hex(32))"

# 3. 配置 PostgreSQL 密码
echo "你的PG密码" > secrets/pg_password.txt

# 4. 启动（CPU 模式）
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d

# 5. 等待健康（首次需拉取 Ollama 模型，约 5-15 分钟）
docker ps --format "{{.Names}}: {{.Status}}"
# 确认全部 healthy

# 6. 首次初始化 ingest 目录权限（named volume 继承可能不准）
docker exec -u root edagent-agent-api chown -R agent:agent /data/ingest/

# 7. 访问
# Web UI:  http://localhost:18088/
# API:     http://localhost:18088/api/v1/
# Grafana: http://localhost:13000/
# Jaeger:  http://localhost:16686/
```

### 14.3 切换到在线 LLM

编辑 `.env`：

```bash
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=sk-xxx
LLM_MODEL=deepseek-chat

EMBEDDING_BASE_URL=https://api.openai.com/v1
EMBEDDING_API_KEY=sk-xxx
EMBEDDING_MODEL=text-embedding-3-small
```

重启 agent-api：
```bash
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d agent-api
```

### 14.4 完整数据卷清单

本项目共 10 个容器、7 个命名数据卷：

| 数据卷 | 容器 | 挂载点 | 内容 | 迁移必要性 |
|---|---|---|---|---|
| `edagent_pg_data` | postgres | `/var/lib/postgresql/data` | 业务表 + LangGraph 检查点 | **必须** |
| `edagent_chroma_data` | chromadb | `/data` | 向量索引（RAG 文档嵌入） | **必须** |
| `edagent_ollama_data` | ollama | `/root/.ollama` | LLM + Embedding 模型权重 | **必须**（否则重新拉取约 5GB） |
| `edagent_ingest_data` | agent-api | `/data/ingest` | 用户上传的原始文档 | 推荐 |
| `edagent_redis_data` | redis | `/data` | 缓存（非持久业务数据） | 可选 |
| `edagent_grafana_data` | grafana | `/var/lib/grafana` | 仪表盘配置 | 可选（配置已在 config/ 自动供给） |
| `edagent_prom_data` | prometheus | `/prom` | 历史指标 | 可选 |

### 14.5 完整迁移指南（旧机 → 新机）

> 目标：在新电脑上从零恢复到完全可用状态，包含全部用户数据、会话历史、知识库文档和已下载模型。

#### 阶段一：旧机备份（约 10-30 分钟）

```bash
cd c:\djangoproject\EdAgent

# 1. 停止所有服务（保证数据一致性）
docker compose -f docker-compose.yml -f docker-compose.cpu.yml down

# 2. 创建备份目录
mkdir -p edagent-backup

# 3. 逐个导出数据卷
#    Windows PowerShell 逐条执行：
docker run --rm -v edagent_pg_data:/data -v "$(pwd)/edagent-backup:/backup" alpine tar czf /backup/pg_data.tar.gz -C /data .
docker run --rm -v edagent_chroma_data:/data -v "$(pwd)/edagent-backup:/backup" alpine tar czf /backup/chroma_data.tar.gz -C /data .
docker run --rm -v edagent_ollama_data:/data -v "$(pwd)/edagent-backup:/backup" alpine tar czf /backup/ollama_data.tar.gz -C /data .
docker run --rm -v edagent_ingest_data:/data -v "$(pwd)/edagent-backup:/backup" alpine tar czf /backup/ingest_data.tar.gz -C /data .
docker run --rm -v edagent_grafana_data:/data -v "$(pwd)/edagent-backup:/backup" alpine tar czf /backup/grafana_data.tar.gz -C /data .

# 4. 导出 PostgreSQL 逻辑备份（双保险）
docker run --rm --network edagent_net -v "$(pwd)/edagent-backup:/backup" \
  postgres:16-alpine pg_dump -h postgres -U agent ai_agent > edagent-backup/pg_dump.sql
#   如果上面因网络报错，先启动 postgres 再导：
#   docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d postgres
#   docker exec edagent-postgres pg_dump -U agent ai_agent > edagent-backup/pg_dump.sql
#   docker compose -f docker-compose.yml -f docker-compose.cpu.yml down

# 5. 拷贝配置文件
cp .env edagent-backup/.env
cp secrets/pg_password.txt edagent-backup/pg_password.txt
```

#### 阶段二：传输到新机

将以下内容打包传输（U盘 / 网络传输，总计约 5-10GB）：

```
edagent-backup/
├── pg_data.tar.gz          # PostgreSQL 数据卷（业务表+检查点）
├── chroma_data.tar.gz       # ChromaDB 向量索引
├── ollama_data.tar.gz       # 模型权重（最大，约5GB）
├── ingest_data.tar.gz       # 用户上传文档
├── grafana_data.tar.gz      # Grafana 配置
├── pg_dump.sql              # PostgreSQL 逻辑备份（双保险）
├── .env                     # 环境变量（含密钥）
└── pg_password.txt          # PG 密码文件
```

同时需要项目代码（git clone 或拷贝项目目录）。

#### 阶段三：新机恢复（约 30-60 分钟）

```bash
# 1. 前置条件：安装 Docker Desktop（Windows）或 Docker Engine（Linux）
#    Windows 确认 WSL2 后端已启用

# 2. 获取项目代码
git clone <repo-url> EdAgent && cd EdAgent
#    或直接拷贝项目目录

# 3. 恢复配置文件
cp edagent-backup/.env .env
cp edagent-backup/pg_password.txt secrets/pg_password.txt

# 4. 创建空数据卷
docker volume create edagent_pg_data
docker volume create edagent_chroma_data
docker volume create edagent_ollama_data
docker volume create edagent_ingest_data
docker volume create edagent_grafana_data

# 5. 逐个恢复数据卷内容
#    Windows PowerShell 逐条执行：
docker run --rm -v edagent_pg_data:/data -v "$(pwd)/edagent-backup:/backup" alpine sh -c "cd /data && tar xzf /backup/pg_data.tar.gz"
docker run --rm -v edagent_chroma_data:/data -v "$(pwd)/edagent-backup:/backup" alpine sh -c "cd /data && tar xzf /backup/chroma_data.tar.gz"
docker run --rm -v edagent_ollama_data:/data -v "$(pwd)/edagent-backup:/backup" alpine sh -c "cd /data && tar xzf /backup/ollama_data.tar.gz"
docker run --rm -v edagent_ingest_data:/data -v "$(pwd)/edagent-backup:/backup" alpine sh -c "cd /data && tar xzf /backup/ingest_data.tar.gz"
docker run --rm -v edagent_grafana_data:/data -v "$(pwd)/edagent-backup:/backup" alpine sh -c "cd /data && tar xzf /backup/grafana_data.tar.gz"

# 6. 构建 agent-api 镜像（前端SPA + Python 依赖，约5-10分钟）
docker compose -f docker-compose.yml -f docker-compose.cpu.yml build agent-api

# 7. 启动全部服务
#    Ollama 不需要重新拉取模型（已从备份恢复）
#    PostgreSQL 不需要重新建表（已从备份恢复）
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d

# 8. 等待全部 healthy
docker ps --format "table {{.Names}}\t{{.Status}}"
#    确认 10 个容器全部 Up (healthy)

# 9. 修复 ingest 目录权限
docker exec -u root edagent-agent-api chown -R agent:agent /data/ingest/

# 10. 验证
curl http://localhost:18088/                          # SPA
curl -s http://localhost:18088/health                  # 健康检查
#    浏览器打开 http://localhost:18088/ 登录，检查会话历史和知识库文档是否完好
```

#### 阶段四：故障排除

| 症状 | 原因 | 解决 |
|---|---|---|
| PostgreSQL 启动失败 | 数据卷权限或版本不一致 | 删卷重建，用 `pg_dump.sql` 恢复：`docker exec -i edagent-postgres psql -U agent ai_agent < pg_dump.sql` |
| Ollama 模型不可见 | 数据卷恢复路径不对 | `docker exec edagent-ollama ollama list` 确认，无则重新 `ollama pull` |
| ChromaDB 报集合不存在 | 向量索引未恢复 | 重新上传文档到 `/api/v1/rag/documents` |
| agent-api 启动报错连不上 PG | 启动顺序问题 | `docker compose up -d postgres` 先等 healthy，再 `up -d agent-api` |
| Nginx 502 | agent-api 未就绪 | 等 30 秒重试，或 `docker logs edagent-agent-api --tail 20` |

#### 精简迁移（仅核心数据，不含模型）

如果不迁移 Ollama 模型（新机重新拉取），只需备份和恢复 3 个数据卷：

```bash
# 仅备份核心数据（约 500MB）
edagent-backup/
├── pg_data.tar.gz          # 必须：用户/会话/消息/检查点
├── chroma_data.tar.gz      # 必须：向量索引
├── ingest_data.tar.gz      # 推荐：原始文档
├── pg_dump.sql             # 双保险
├── .env
└── pg_password.txt

# 新机启动时 Ollama 会自动拉取模型（约 5-15 分钟下载）
# 其余恢复步骤同上，跳过 ollama_data 恢复即可
```

## 15. 开发常用命令

```bash
# 重建镜像（修改后端或前端代码后）
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d --build agent-api

# 查看日志
docker logs edagent-agent-api --tail 50 -f

# 进入容器调试
docker exec -it edagent-agent-api bash

# 查看数据库
docker exec -it edagent-postgres psql -U agent -d ai_agent

# 测试 SSE
curl -N -b cookie.txt http://localhost:18088/api/v1/ui/chat/stream \
  -d '{"message":"你好"}' -H "Content-Type: application/json"

# 查看容器健康
docker ps --format "table {{.Names}}\t{{.Status}}"

# 停止全部
docker compose -f docker-compose.yml -f docker-compose.cpu.yml down

# 清理数据（慎重！）
docker compose -f docker-compose.yml down -v
```

## 16. 已知坑点与注意事项

| # | 坑点 | 原因 | 修复 |
|---|---|---|---|
| 1 | SSE 报 `NotImplementedError` | 同步 PostgresSaver 不支持异步图 | 改用 AsyncPostgresSaver |
| 2 | `TypeError: run_sync() unexpected kwarg` | anyio 不透传 kwargs | 用 functools.partial 包装 |
| 3 | Chroma `exactly one operator` | 多键 where 不接受平铺 | 用 `{"$and": [...]}` |
| 4 | 前端永远停在"推理中" | reactive() 对象误用 .value | reactive 直接访问属性，不用 .value |
| 5 | embedding 400 "invalid input type" | tokenizer 长度检查不兼容 | OpenAIEmbeddings 设 check_embedding_ctx_length=False |
| 6 | Rollup 构建报 "not exported" | @element-plus/icons-vue 无 Calculator | 改用 DataAnalysis |
| 7 | Grafana 13000 页面 404 | 浏览器 Vite Service Worker 残留 | 无痕窗口或清除 SW |
| 8 | PowerShell 命令被拦 | ExecutionPolicy Restricted | 拆成单条简单命令 |

## 17. Skills 索引

项目内 `.trae/skills/` 下有 5 个 Trae Skill，在对话中自动匹配加载：

| Skill | 触发场景 |
|---|---|
| langgraph-agent-builder | 创建 Agent 图、定义节点和边 |
| rag-pipeline-builder | 构建 RAG 检索管道 |
| observability-setup | 配置 Prometheus/Grafana/OTel |
| docker-compose-generator | 生成 Docker Compose 配置 |
| edagent-user-ui-sse | 用户系统/SSE/UI 排错 |

## 18. 后续扩展方向

- **模型升级**：更换 `.env` 中 `LLM_MODEL`，Ollama 自动拉取（如 qwen2.5:14b）
- **多用户权限**：增加角色表（admin/user），管理端点
- **文档格式扩展**：在 ingest.py 增加 DOCX/XLSX 解析器
- **对话导出**：新增 `/ui/conversations/{id}/export` 导出 Markdown
- **流式取消**：前端 AbortController 已实现，后端可增加中断信号
- **多 Agent 协作**：LangGraph 支持 subgraph，可在图中编排多个 Agent
- **模型微调**：Ollama 支持自定义 Modelfile，基于现有模型微调
