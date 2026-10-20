# EdAgent 项目文档

> 本地化 AI Agent 平台：LangGraph 工作流 + RAG 知识库 + Web UI + 用户系统，全部 Docker 化运行。
> 支持本地（容器内 Ollama）与云端（VPS + 云端 LLM API）双模式部署。
> 最后更新：2026-10-20（新增云端部署模式与公网注册开关 `AGENT_ALLOW_REGISTER`；2026-10-15 完成 Phase 3 能力扩展：插件系统／批量导入／RAG 调参／会话标签分类／WebSocket 协作围观）。

## 1. 项目概述

EdAgent 是一个基于 LangGraph 的本地 AI Agent，支持：

- **智能路由**：自动判断简单问答 / 工具调用 / RAG 检索 / 多 Agent 协作（team）
- **流式输出**：SSE 逐 token 打字机效果 + 思考过程展示 + 客户端中断即时取消（部分答案自动落库）
- **用户系统**：注册 / 登录 / 会话历史持久化（Cookie Session）+ admin/user 角色 + 管理后台页面（前端 `/admin` 页面，用户列表/角色切换/删除）
- **RAG 知识库**：按用户隔离的文档上传与检索（PDF/Word/Excel/PPTX/CSV/MD/TXT/HTML）
- **多 Agent 协作**：「规划者 → 执行者 → 审稿者」子图处理复杂分析题（`AGENT_TEAM_MAX_ROUNDS` 与 planner/reviewer 提示词均可通过 `.env` 配置，默认 2 轮修订）
- **会话置顶/搜索**：会话列表标题关键词模糊搜索 + 置顶排序
- **会话组织**：多标签 / 单值分类 / 收藏三维度组织与筛选（标签为 JSONB 数组，支持包含查询）
- **协作围观**：生成分享链接供他人只读围观会话，WebSocket 实时同步提问 / 思考 / 逐 token 输出与在线成员
- **会话导出**：一键导出会话为 Markdown / HTML（含路由/思考/来源/工具调用，可打印为 PDF）
- **能力扩展**：进程内 Python 插件（热加载 `plugins/*.py` 的 `@tool`）、批量文档导入（逐文件进度轮询）、RAG 检索调参
- **可观测性**：Prometheus 指标 + OpenTelemetry/Jaeger 链路追踪
- **双通道鉴权**：Web UI 走 Cookie Session，API 脚本走 Bearer API Key
- **注册开关**：`AGENT_ALLOW_REGISTER` 控制是否开放注册（云端默认关闭）；库中无用户时始终放行以引导首个 admin
- **双模式部署**：本地模式（容器内 Ollama 推理）与云端模式（个人 VPS + 云端 LLM API），见第 6.4 / 14.6 节

LLM 统一走 OpenAI 兼容协议：本地模式默认容器内 Ollama（deepseek-r1:8b + nomic-embed-text），云端模式默认外部 API（如 DeepSeek），仅通过环境变量切换，无需改代码。

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
│   ├── db.py                     #    业务数据库（pg 连接池 + 建表 + CRUD + 角色管理）
│   ├── api/
│   │   ├── routes.py             #    API 路由（/chat /agent /rag/*，含 /rag/batch 批量导入）
│   │   ├── auth_routes.py        #    认证路由（/auth/register /login /logout /me）
│   │   ├── ui_routes.py          #    UI 路由（会话 CRUD/组织维度/分享 + /ui/chat/stream SSE + /ui/ws 协作围观）
│   │   ├── team_routes.py        #    Team OS 路由（团队/任务/PR/通知）
│   │   ├── plugin_routes.py      #    插件管理路由（/plugins 列表/上传/删除，仅 admin）
│   │   ├── admin_routes.py       #    管理路由（/admin/users 列表/改角色/删除，仅 admin）
│   │   ├── broadcast.py          #    协作围观房间广播（进程内单例 CollaborationHub）
│   │   └── schemas.py            #    Pydantic 请求/响应模型
│   ├── agents/
│   │   ├── graph.py              #    LangGraph 主图组装（router→answer/rag/react/team→report）
│   │   ├── state.py              #    Agent 状态定义（TypedDict）
│   │   ├── nodes.py              #    各节点实现（async：router/direct/agent/tools/report）
│   │   ├── multi_agent.py        #    多 Agent 协作子图（planner→worker→reviewer）
│   │   └── tools.py              #    Agent 工具（knowledge_search 等）
│   ├── llm/
│   │   └── client.py             #    LLM 客户端（OpenAI 兼容协议，chat_invoke/ainvoke）
│   ├── rag/
│   │   ├── ingest.py             #    文档摄取（PDF/DOCX/XLSX/PPTX/CSV/MD/TXT/HTML→分块→ChromaDB，按 owner 隔离）
│   │   └── retriever.py          #    检索器（mmr / similarity / similarity_score_threshold，按 user_id 过滤 owner）
│   ├── plugins/
│   │   └── loader.py             #    进程内 Python 插件加载（扫描 /app/plugins/*.py 的 @tool）
│   ├── team/                     #    Team OS（多角色协作子系统）
│   │   ├── roles.py              #    角色注册表（roles/*.yaml 幂等同步到 agent_roles 表）
│   │   ├── roles/                #    6 个角色定义（pm / product / architect / developer / tester / reviewer）
│   │   ├── runner.py             #    团队运行器（PM 拆解 → DAG 调度 → 汇总产物）
│   │   ├── scheduler.py          #    拓扑排序 + Semaphore 并发调度 + 人工确认阻塞
│   │   ├── nodes.py              #    角色执行节点
│   │   ├── patch.py              #    代码块解析 + unified diff 生成/应用
│   │   ├── sandbox.py            #    Docker 沙箱（一次性容器编译校验，失败可降级）
│   │   ├── templates.py          #    团队模板库（software_dev / content_creation / data_analysis）
│   │   └── state.py              #    Team 状态定义
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
│           ├── Admin.vue        #    管理后台页（用户列表/角色切换/删除，仅 admin）
│           ├── Team.vue         #    团队协作页（团队/任务/PR/通知）
│           └── Chat.vue         #    聊天主页面（会话侧栏/组织维度筛选/SSE/Markdown/知识库抽屉/协作围观）
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
├── plugins/                      # 进程内 Python 插件目录（bind mount 到容器 /app/plugins）
│   └── example_plugin.py         #    示例插件（random_number）
├── docker-compose.yml            # 主编排文件
├── docker-compose.cpu.yml        # CPU 模式覆盖
├── docker-compose.gpu.yml        # GPU 模式覆盖
├── docker-compose.cloud.yml      # 云端模式覆盖（去掉 Ollama，可观测组件按 profile 裁剪）
├── Dockerfile                    # 三阶段构建（Node→Python→Runtime）
├── pyproject.toml                # Python 依赖
├── .env / .env.example           # 环境变量（本地模式）
├── .env.cloud                    # 环境变量模板（云端模式；需填 API Key 并更换全部密钥）
├── PROJECT.md                    # 本文件（项目权威参考文档：架构 / 配置 / Schema / API / 部署）
└── HANDOFF.md                    # 会话交接文档（进度 / 待办 / 踩坑记录，随会话更新）
```

## 4. 技术栈

| 层         | 技术                                           | 版本                 |
| --------- | -------------------------------------------- | ------------------ |
| 后端框架      | FastAPI + uvicorn                            | ≥0.115 / workers=1 |
| Agent 框架  | LangGraph + LangChain                        | ≥0.2 / ≥0.3        |
| LLM 协议    | OpenAI 兼容（Ollama / DeepSeek / 通义 / 硅基流动等）      | —                  |
| LLM（本地模式） | deepseek-r1:8b                               | 容器内 Ollama        |
| LLM（云端模式） | deepseek-chat（模板默认，可换任意兼容端点模型）              | 外部 API            |
| Embedding（本地） | nomic-embed-text（768 维）                       | 容器内 Ollama        |
| Embedding（云端） | BAAI/bge-large-zh-v1.5（1024 维，模板默认）           | 外部 API            |
| 数据库       | PostgreSQL 16                                | psycopg3 连接池       |
| 向量库       | ChromaDB                                     | ≥0.5               |
| 缓存        | Redis 7.2                                    | —                  |
| 前端框架      | Vue 3.5 + Vite 5                             | SPA                |
| UI 库      | Element Plus 2.8                             | 中文 zhCn            |
| 状态管理      | Pinia 2                                      | —                  |
| 路由        | Vue Router 4                                 | history 模式         |
| Markdown  | marked + DOMPurify                           | —                  |
| 鉴权        | Cookie Session（itsdangerous）+ Bearer API Key | —                  |
| 密码哈希      | PBKDF2-HMAC-SHA256                           | 标准库                |
| 容器        | Docker / Docker Compose                      | Compose ≥v2.24     |
| 可观测性      | Prometheus + OTel + Jaeger + Grafana         | —                  |

## 5. 配置详解

### 5.1 环境变量（.env）

复制 `.env.example` 为 `.env` 并修改密码：

```bash
cp .env.example .env
```

| 变量                     | 默认值                      | 说明                |
| ---------------------- | ------------------------ | ----------------- |
| **端口映射**               | <br />                   | <br />            |
| `OLLAMA_HOST_PORT`     | 11534                    | Ollama 宿主机端口      |
| `CHROMA_HOST_PORT`     | 18000                    | ChromaDB 宿主机端口    |
| `POSTGRES_HOST_PORT`   | 15432                    | PostgreSQL 宿主机端口  |
| `REDIS_HOST_PORT`      | 16379                    | Redis 宿主机端口       |
| `AGENT_API_PORT`       | 18080                    | agent-api 直连端口    |
| `NGINX_HOST_PORT`      | 18088                    | **Web 入口**        |
| `OTEL_GRPC_HOST_PORT`  | 14317                    | OTel gRPC         |
| `OTEL_HTTP_HOST_PORT`  | 14318                    | OTel HTTP         |
| `JAEGER_UI_HOST_PORT`  | 16686                    | Jaeger UI         |
| `PROMETHEUS_HOST_PORT` | 19090                    | Prometheus        |
| `GRAFANA_HOST_PORT`    | 13000                    | Grafana           |
| **密码/密钥**              | <br />                   | <br />            |
| `PG_PASSWORD`          | change\_me               | PostgreSQL 密码     |
| `AGENT_API_KEY`        | change\_me               | Bearer API Key    |
| `AGENT_SESSION_SECRET` | change\_me               | Cookie 签名密钥       |
| `GRAFANA_PASSWORD`     | change\_me               | Grafana 管理员密码     |
| **注册**                | <br />                   | <br />            |
| `AGENT_ALLOW_REGISTER` | true                     | 是否开放注册；公网建议 false。库中尚无用户时始终放行，用于引导首个 admin |
| **LLM**                | <br />                   | <br />            |
| `LLM_BASE_URL`         | <http://ollama:11434/v1> | LLM API 地址        |
| `LLM_API_KEY`          | ollama                   | LLM API Key       |
| `LLM_MODEL`            | deepseek-r1:8b           | 模型名               |
| **Embedding**          | <br />                   | <br />            |
| `EMBEDDING_BASE_URL`   | <http://ollama:11434/v1> | Embedding API 地址  |
| `EMBEDDING_API_KEY`    | ollama                   | Embedding API Key |
| `EMBEDDING_MODEL`      | nomic-embed-text         | 嵌入模型              |
| **RAG**                | <br />                   | <br />            |
| `AGENT_RAG_SEARCH_TYPE`    | mmr                  | 检索类型：mmr / similarity / similarity_score_threshold（非法值回退 mmr） |
| `AGENT_RAG_SCORE_THRESHOLD` | 0.0                 | 相似度分数阈值，仅 similarity_score_threshold 生效 |
| **CPU**                | <br />                   | <br />            |
| `OLLAMA_NUM_THREADS`   | 8                        | Ollama CPU 线程数    |
| **多 Agent 协作 / Team OS** | <br />              | <br />            |
| `AGENT_TEAM_MAX_ROUNDS`    | 2                     | 多 Agent 协作审稿最大轮次 |
| `AGENT_TEAM_PLANNER_PROMPT`  | (留空用默认)         | 规划者提示词         |
| `AGENT_TEAM_REVIEWER_PROMPT` | (留空用默认)         | 审稿者提示词         |
| `AGENT_TEAM_MAX_PARALLEL`    | 2                  | Team OS 并发执行的任务数 |
| `AGENT_TEAM_SANDBOX_ENABLED` | true               | 是否启用 Docker 沙箱验证 |
| `AGENT_TEAM_SANDBOX_IMAGE`   | edagent-agent-api:0.1.0 | 沙箱执行镜像       |
| `AGENT_TEAM_SANDBOX_TIMEOUT` | 120                | 沙箱执行超时（秒）      |

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
    # 多 Agent 协作 / Team OS
    team_max_rounds: int = 2 / team_planner_prompt: str / team_reviewer_prompt: str
    team_max_parallel: int = 2 / team_sandbox_enabled: bool = True
    team_sandbox_image: str / team_sandbox_timeout: int = 120
    # RAG
    rag_chunk_size: 512 / rag_chunk_overlap: 50 / rag_top_k: 5 / rag_mmr_lambda_mult: 0.5
    rag_search_type: str = "mmr" / rag_score_threshold: float = 0.0
    # 可观测性
    otel_exporter_otlp_endpoint（空值则不向 collector 导出）/ otel_service_name
    # 安全
    api_key / session_secret / session_max_age_seconds (7天) / allow_register / static_dir
```

### 5.3 Secrets

PostgreSQL 密码通过 Docker secrets 注入（`secrets/pg_password.txt`），不写在环境变量明文中。

## 6. Docker 服务

### 6.1 服务清单

| 服务             | 镜像                                   | 容器名                    | 端口          | 依赖                     |
| -------------- | ------------------------------------ | ---------------------- | ----------- | ---------------------- |
| ollama         | ollama/ollama:0.32.3                 | edagent-ollama         | 11534       | —                      |
| chromadb       | chromadb/chroma:1.5.9                | edagent-chromadb       | 18000       | —                      |
| postgres       | postgres:16-alpine                   | edagent-postgres       | 15432       | —                      |
| redis          | redis:7.2-alpine                     | edagent-redis          | 16379       | —                      |
| agent-api      | 本地构建                                 | edagent-agent-api      | 18080       | ollama+chroma+pg+redis |
| nginx          | nginx:1.27-alpine                    | edagent-nginx          | 18088       | agent-api              |
| otel-collector | otel/opentelemetry-collector-contrib | edagent-otel-collector | 14317/14318 | jaeger                 |
| jaeger         | jaegertracing/jaeger:2.6.0           | edagent-jaeger         | 16686       | —                      |
| prometheus     | prom/prometheus:v2.54.1              | edagent-prometheus     | 19090       | —                      |
| grafana        | grafana/grafana-oss:11.3.0           | edagent-grafana        | 13000       | prometheus             |

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

### 6.4 云端模式 override（docker-compose.cloud.yml）

个人 VPS + 云端 LLM API 场景（不在容器内跑 Ollama），在基础编排文件之上叠加 override 文件：

```bash
# 云端模式（必须带 --env-file，否则会读到本地 .env）
docker compose --env-file .env.cloud -f docker-compose.yml -f docker-compose.cloud.yml up -d

# 本地模式命令不变
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d
```

- **依赖版本**：Docker Compose v2.24+（override 文件用 `!override` / `!reset` 标签做字段整体替换，而非默认的字段合并）
- **默认不启动的服务**：`ollama` 用 `profiles: ["local-only"]`；`otel-collector` / `jaeger` / `prometheus` / `grafana` 用 `profiles: ["observability"]`（需要排障时追加 `--profile observability` 启用）
- **agent-api 覆盖**：
  - `depends_on` 用 `!override` 去掉 ollama 依赖，只保留 chroma/postgres/redis
  - 环境变量：`AGENT_OTEL_EXPORTER_OTLP_ENDPOINT=""`（空值短路，不向 collector 导出）、`AGENT_COLLECTION_NAME=agent_edagent_cloud`（换 embedding 模型后向量维度变化，必须换新 collection）、`AGENT_TEAM_SANDBOX_ENABLED=false`
  - `volumes` 用 `!override` **移除 docker.sock 挂载**（沙箱随之禁用，最小化攻击面）、`group_add: !reset []`
- **nginx 覆盖**：`ports` 用 `!override` 改为 `${NGINX_HOST_PORT:-80}:80`（对公网暴露；基础文件里绑定的是 127.0.0.1）
- **TLS**：`config/nginx.conf` 目前只 listen 80，配置域名后需自行加 443 + 证书

## 7. 数据库 Schema

复用同一个 PostgreSQL 实例，业务表与 LangGraph checkpoints 表并存。

### 7.1 业务表（app/db.py）

```sql
-- 用户
CREATE TABLE users (
    id            BIGSERIAL PRIMARY KEY,
    username      VARCHAR(32) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,           -- pbkdf2_sha256$240000$salt$hash
    role          VARCHAR(16) NOT NULL DEFAULT 'user',  -- admin / user（首个注册用户自动为 admin）
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- 会话
CREATE TABLE conversations (
    id          UUID PRIMARY KEY,
    user_id     BIGINT REFERENCES users(id) ON DELETE CASCADE,
    title       VARCHAR(100),
    thread_id   VARCHAR(80) UNIQUE,             -- 对应 LangGraph thread_id
    pinned      BOOLEAN NOT NULL DEFAULT FALSE, -- 置顶
    tags        JSONB NOT NULL DEFAULT '[]',    -- 多标签（jsonb 数组）
    category    VARCHAR(32),                    -- 单值分类（可空）
    favorite    BOOLEAN NOT NULL DEFAULT FALSE, -- 收藏
    share_token VARCHAR(40),                    -- 协作围观分享 token（为空=未开启）
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);
-- 部分唯一索引：仅对已开启分享的会话保证 token 唯一
CREATE UNIQUE INDEX idx_conversations_share_token
    ON conversations(share_token) WHERE share_token IS NOT NULL;

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

-- ==================== Team OS（多角色团队协作） ====================
-- 角色注册表：启动时由 app/team/roles/*.yaml 幂等同步（upsert）
CREATE TABLE agent_roles (
    name VARCHAR(32) PRIMARY KEY,      -- pm/product/architect/developer/tester/reviewer
    display_name VARCHAR(64), description TEXT, system_prompt TEXT,
    allowed_tools JSONB DEFAULT '[]', artifact_types JSONB DEFAULT '[]',
    requires_human_confirm BOOLEAN DEFAULT FALSE, is_builtin BOOLEAN DEFAULT TRUE
);

-- 团队：一个用户可拥有多个团队，互相隔离
CREATE TABLE teams (
    id UUID PRIMARY KEY, user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(100), template VARCHAR(32),   -- software_dev/content_creation/data_analysis
    created_at/updated_at TIMESTAMPTZ
);

-- 团队任务：DAG 节点（depends_on 决定拓扑顺序）
CREATE TABLE team_tasks (
    id UUID PRIMARY KEY, team_id UUID, conversation_id UUID, user_id BIGINT,
    role VARCHAR(32), title VARCHAR(200), description TEXT,
    status VARCHAR(16),                -- pending/running/blocked/done/failed/cancelled
    depends_on JSONB DEFAULT '[]', artifact_id UUID, error TEXT
);

-- 产物：PRD/架构文档/代码/测试报告/评审意见/沙箱验证报告
CREATE TABLE team_artifacts (
    id UUID PRIMARY KEY, task_id UUID REFERENCES team_tasks(id) ON DELETE CASCADE,
    type VARCHAR(32), title VARCHAR(200), content TEXT, version INT DEFAULT 1
);

-- PR / 补丁：开发产物解析成 unified diff，评审后可 apply/reject
CREATE TABLE team_prs (
    id UUID PRIMARY KEY, team_id UUID, task_id UUID REFERENCES team_tasks(id) ON DELETE CASCADE,
    title VARCHAR(200), diff_content TEXT, files_changed JSONB DEFAULT '[]',
    status VARCHAR(16),                -- pending/approved/needs_changes/applied/rejected
    reviewer_notes TEXT
);

-- 通知：任务阻塞/失败/团队完成时写入，前端铃铛展示未读数
CREATE TABLE notifications (
    id BIGSERIAL PRIMARY KEY, user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    team_id UUID, task_id UUID,
    type VARCHAR(32),                  -- task_blocked/task_failed/team_done
    title VARCHAR(200), body TEXT, is_read BOOLEAN DEFAULT FALSE
);

-- ==================== 批量导入 ====================
-- 批量导入任务：逐文件状态记在 results(JSONB)，供前端轮询进度
CREATE TABLE ingest_jobs (
    id UUID PRIMARY KEY, user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    status VARCHAR(16) DEFAULT 'running',        -- running/done/failed
    total_files INT NOT NULL, completed_files INT DEFAULT 0, failed_files INT DEFAULT 0,
    results JSONB DEFAULT '[]',                  -- 逐文件状态（文件名/成功/错误）
    created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now()
);
```

> `conversations` 的 `pinned` / `tags` / `category` / `favorite` / `share_token` 均为老库升级列，
> 由 `app/db.py` 初始化流程中的幂等 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 迁移补齐（含上述部分唯一索引）。

### 7.2 LangGraph 检查点表

由 `AsyncPostgresSaver.setup()` 自动创建（`checkpoints`、`writes`、`migrations`），幂等执行。

## 8. Agent 图结构

```
START → router
         ├── simple → direct_answer → report → END
         ├── rag    → retrieve → agent ↔ tools → report → END
         ├── tool   → agent ↔ tools → report → END
         └── team   → [子图: planner → worker ↔ tools → reviewer]
                          reviewer --revise--> worker（最多 2 轮）
                          reviewer --approve--> END → report → END
```

| 节点              | 职责                                        | 异步              |
| --------------- | ----------------------------------------- | --------------- |
| `router`        | 判断任务类型（简单/工具/RAG/team）                    | async + ainvoke |
| `direct_answer` | 直接 LLM 回答                                 | async + ainvoke |
| `retrieve`      | ChromaDB 语义检索（按 user\_id 过滤）              | async           |
| `agent`         | ReAct 循环（思考→行动→观察）                        | async + ainvoke |
| `tools`         | 工具执行（knowledge\_search 等）                 | sync            |
| `team`          | 多 Agent 协作子图包装节点（见下）                      | async + ainvoke |
| `report`        | 结构化输出（answer/route/sources/tool\_results） | sync            |

**多 Agent 协作子图**（[multi\_agent.py](app/agents/multi_agent.py)，TeamState 独立 schema）：

| 子图节点       | 职责                                                           | <br />                                                                  |
| ---------- | ------------------------------------------------------------ | :---------------------------------------------------------------------- |
| `planner`  | 把复杂问题拆解为 2-4 个分析要点（内部消息，不推给前端）                               | <br />                                                                  |
| `worker`   | 复用主图 `agent_node`（可调工具），token 以 `langgraph_node="worker"` 冒泡 | <br />                                                                  |
| `reviewer` | 审稿：输出 \`{"verdict": "approve                                 | revise", "notes"}`；revise 时附意见退回 worker；达到 ` MAX\_TEAM\_ROUNDS=2\` 强制放行 |

子图经 `team_node` 包装（透传 config 保证 user\_id 隔离与 token 事件冒泡），只把最终 AI 草稿回填主图。

**关键**：需要流式输出的节点必须 `async def` + `ainvoke`，否则 `astream_events` 的 token 级事件不会冒泡；子图内需流式的节点名要加入 `ui_routes._STREAM_NODES`。

## 9. API 端点

### 9.1 认证（/api/v1/auth）

| 方法   | 路径        | 鉴权 | 说明                                      |
| ---- | --------- | -- | --------------------------------------- |
| POST | /register | 公开 | 注册（用户名3-32字符，密码≥6）；**首个注册用户自动成为 admin**；`AGENT_ALLOW_REGISTER=false` 时返回 403（库中无用户时仍放行） |
| GET  | /config   | 公开 | 前端据此决定是否展示注册入口，返回 `{allow_register}` |
| POST | /login    | 公开 | 登录（写 Session Cookie）                    |
| POST | /logout   | 登录 | 登出（清除 Session）                          |
| GET  | /me       | 登录 | 获取当前用户（含 role）                          |

### 9.2 UI 专用（/api/v1/ui）

| 方法     | 路径                         | 鉴权 | 说明                                |
| ------ | -------------------------- | -- | --------------------------------- |
| GET    | /conversations             | 登录 | 会话列表（仅本人，置顶优先排序）                  |
| GET    | /conversations?q=keyword   | 登录 | 搜索会话（标题模糊匹配）                      |
| GET    | /conversations/{id}        | 登录 | 会话历史消息                            |
| GET    | /conversations/{id}/export?format=md\|html | 登录 | 导出会话（md/html，可打印为 PDF）             |
| POST   | /conversations/{id}/pin    | 登录 | 切换会话置顶                            |
| DELETE | /conversations/{id}        | 登录 | 删除会话（隔离校验）                        |
| PATCH  | /conversations/{id}        | 登录 | 重命名会话                             |
| POST   | /chat/stream               | 登录 | **SSE 流式聊天**（客户端断连即取消执行，部分答案自动落库） |
| GET    | /conversations?tag=&category=&favorite= | 登录 | 组合筛选（tag 走 jsonb 包含；`favorite=1` 仅收藏；可与 `q` 组合） |
| GET    | /conversation-filters      | 登录 | 聚合当前用户用过的分类与标签及其计数（供筛选下拉） |
| PATCH  | /conversations/{id}/meta   | 登录 | 局部更新组织维度（tags/category/favorite，**只改请求体显式出现的字段**） |
| GET    | /conversations/{id}/share  | 登录 | 读取分享状态                                  |
| POST   | /conversations/{id}/share  | 登录 | 开启协作围观，返回 `{ok, enabled, share_token, share_url, viewers}` |
| DELETE | /conversations/{id}/share  | 登录 | 关闭分享（清空 token）并**强制解散**已打开的围观连接        |
| GET    | /shared/{share_token}      | 登录 | 只读读取共享会话及其消息（返回 `shared: true` 与 `owner_username`） |
| WS     | /ws/{share_token}          | 登录 | 协作围观 WebSocket（只读）                       |

`PATCH /conversations/{id}/meta` 请求体的 `tags` / `category` / `favorite` 三者均可选，只更新请求体中显式出现的字段（`tags: []` 清空标签，`category: ""` 清空分类）；`POST .../share` 返回的 `share_url` 形如 `/?c=<id>&share=<token>`。

**协作围观 WebSocket（`/ws/{share_token}`）**：事件顺序为 `history` → `presence` / `join` / `leave` → `turn_start` → `reasoning` / `token` → `turn_done` / `turn_error`。鉴权失败时先 `accept` 再推 `error` 事件并 `close(code=4401)`（未登录）/ `4404`（链接无效或已关闭）。只读房间：客户端上行消息被忽略（仅响应心跳）。

### 9.3 API / Agent（/api/v1）

| 方法     | 路径                      | 鉴权      | 说明                           |
| ------ | ----------------------- | ------- | ---------------------------- |
| POST   | /chat                   | API Key | 简单问答                         |
| POST   | /agent                  | API Key | LangGraph Agent（非流式）         |
| POST   | /rag/documents          | 双通道     | 上传文档（Cookie→用户隔离，API Key→全局） |
| DELETE | /rag/documents/{source} | 双通道     | 删除文档                         |
| GET    | /rag/stats              | 双通道     | 知识库统计                        |
| POST   | /rag/query              | 双通道     | RAG 检索/问答                    |
| POST   | /rag/batch              | 双通道     | 批量上传（≤20 文件），后台 `asyncio.create_task` + `Semaphore(2)` 限流 |
| GET    | /rag/batch/{job_id}     | 双通道     | 轮询批量导入进度（逐文件状态）                  |
| GET    | /plugins                | admin   | 插件列表（进程内 Python 插件）               |
| POST   | /plugins                | admin   | 上传插件（限 `.py`、≤1MB；上传后需重启 agent-api 生效） |
| DELETE | /plugins/{name}         | admin   | 删除插件（删除后需重启 agent-api 生效）          |
| GET    | /health                 | 公开      | 健康检查                         |
| GET    | /metrics                | 公开      | Prometheus 指标                |

### 9.4 管理（/api/v1/admin，仅 admin 角色）

| 方法     | 路径               | 说明                                   |
| ------ | ---------------- | ------------------------------------ |
| GET    | /users           | 用户列表（含每人会话数）                         |
| PATCH  | /users/{id}/role | 改角色（admin/user）；禁止改自己；至少保留一名管理员      |
| DELETE | /users/{id}      | 删除用户（级联删除其会话/消息/检查点）；禁止删自己；至少保留一名管理员 |

### 9.5 SSE 事件协议

| 事件          | 数据                                              | 说明          |
| ----------- | ----------------------------------------------- | ----------- |
| `meta`      | `{conversation_id, title}`                      | 会话元数据       |
| `reasoning` | `{delta}`                                       | 思考过程增量      |
| `token`     | `{delta}`                                       | 回答 token 增量 |
| `done`      | `{answer, route, sources, tool_results, model}` | 结构化最终结果     |
| `error`     | `{message}`                                     | 错误          |

### 9.6 团队协作（Team OS，/api/v1/team）

Team OS 是一个**独立于主 Agent 图**的子系统：PM 把目标拆成 WBS（含依赖关系），
`scheduler.py` 做拓扑排序后按 `AGENT_TEAM_MAX_PARALLEL` 并发执行各角色任务，
需要人工确认的角色（pm/product/architect/reviewer）执行完转为 `blocked` 并写通知，等用户确认后继续。

| 方法     | 路径                                        | 说明                                    |
| ------ | ----------------------------------------- | ------------------------------------- |
| GET    | /roles                                    | 角色列表（含 system\_prompt/工具/产物类型/是否需人工确认） |
| GET    | /templates                                | 团队模板列表（软件开发/内容创作/数据分析）                |
| GET    | /list                                     | 我创建的团队（含任务/产物统计）                       |
| POST   | /start                                    | 启动团队协作（body: goal/conversation\_id/template） |
| POST   | /create                                   | 建团队（带 goal 则同时启动）                      |
| GET    | /{team\_id}                               | 任务进度 + 状态汇总                            |
| GET    | /{team\_id}/artifacts                     | 全部产物                                  |
| POST   | /{team\_id}/tasks/{task\_id}/confirm      | 人工确认/拒绝（blocked 任务放行）                  |
| PATCH  | /{team\_id}/rename                        | 团队重命名                                 |
| DELETE | /{team\_id}                               | 删除团队（级联任务/产物/PR）                       |
| GET    | /{team\_id}/prs                           | PR 列表                                 |
| POST   | /{team\_id}/prs/{pr\_id}/apply            | 应用补丁（`patch -p1`，默认目标 `/app`）           |
| POST   | /{team\_id}/prs/{pr\_id}/reject           | 拒绝补丁                                  |
| GET    | /notifications                            | 通知列表（支持 unread\_only）                  |
| GET    | /notifications/unread-count               | 未读数                                   |
| POST   | /notifications/{id}/read                  | 标记已读                                  |
| POST   | /notifications/read-all                   | 全部已读                                  |

**关键实现约束**：固定路径路由（`/templates`、`/list`、`/notifications*`）必须注册在 `/{team_id}` **之前**，
否则会被 `/{team_id}` 通配吞掉。

#### 沙箱执行（可选）

开发者角色产出代码后，会把文件写入一次性 Docker 沙箱做编译验证（`compileall`），
结果存为 `sandbox_report` 产物。沙箱不可用时降级为告警，不阻塞 PR 生成。

- 依赖：容器内 `docker` CLI（Debian 的 `docker-cli` 包）+ 挂载宿主机 `/var/run/docker.sock`
- 权限：Docker Desktop 下 socket 属 `root:root 0660`，非 root 运行的应用需 `group_add: ["0"]`
- **安全提示**：挂载 docker.sock 等价于给该容器宿主机 root 权限，仅适用于本地自托管场景
- 配置：`AGENT_TEAM_SANDBOX_ENABLED`、`AGENT_TEAM_SANDBOX_IMAGE`（默认用本平台镜像，避免依赖镜像源）、`AGENT_TEAM_SANDBOX_TIMEOUT`

## 10. 鉴权系统

### 10.1 双通道设计

| 通道             | 认证方式                     | owner\_id    | RAG 隔离 | 适用场景      |
| -------------- | ------------------------ | ------------ | ------ | --------- |
| Cookie Session | `edagent_session` Cookie | `user-{uid}` | 仅本人文档  | Web UI 用户 |
| Bearer API Key | `Authorization: Bearer`  | `None`（全局）   | 全部文档   | 脚本/自动化    |

### 10.2 中间件顺序

洋葱模型（后 add 更靠外）：OTel → Session → Security → 路由

- Security 中间件仅拦截 `/api/` 开头且不在公开白名单 `{/auth/register, /auth/login, /auth/config}` 的请求
- Session 中间件（itsdangerous）签名 Cookie，`same_site=lax`，`https_only=False`（本地 HTTP）

## 11. RAG 系统

### 11.1 文档摄取

- 支持格式：PDF / Word(docx) / Excel(xlsx，按工作表分页) / PPTX / CSV / Markdown / TXT / HTML
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

| 路径        | 组件           | 守卫           |
| --------- | ------------ | ------------ |
| /login    | Login.vue    | guestOnly    |
| /register | Register.vue | guestOnly    |
| /         | Chat.vue     | requiresAuth |

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

### 12.5 会话组织与协作围观（Chat.vue）

- **侧栏筛选**：「全部 / 收藏」单选 + 「按分类 / 按标签」两个下拉（选项来自 `GET /conversation-filters`）
- **会话行**：显示分类 / 标签徽标 + 收藏星标 + 分享标记，加「更多」菜单（收藏、标签分类、分享协作室、导出 MD/HTML/PDF、删除）
- **编辑弹窗**：「标签 / 分类」编辑（分类单选 + 标签多选 allow-create）
- **分享协作室弹窗**：只读链接 + 复制按钮 + 在线人数轮询 + 开启/关闭分享
- **协作围观视图**：只读（无输入框，底部只读提示条），顶部在线成员头像与「N 人在线」；WS 客户端 25s 心跳，按 44xx 关闭码提示。**在线成员按用户 id 去重**（同一账号多标签页只算 1 人）

## 13. 可观测性

| 组件             | 地址                          | 用途                               |
| -------------- | --------------------------- | -------------------------------- |
| Prometheus     | <http://localhost:19090>    | 指标采集                             |
| Grafana        | <http://localhost:13000>    | 仪表盘（admin / {GRAFANA\_PASSWORD}） |
| Jaeger         | <http://localhost:16686>    | 链路追踪                             |
| OTel Collector | :14317(gRPC) / :14318(HTTP) | 管道转发                             |

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

| 数据卷                    | 容器         | 挂载点                        | 内容                   | 迁移必要性                 |
| ---------------------- | ---------- | -------------------------- | -------------------- | --------------------- |
| `edagent_pg_data`      | postgres   | `/var/lib/postgresql/data` | 业务表 + LangGraph 检查点  | **必须**                |
| `edagent_chroma_data`  | chromadb   | `/data`                    | 向量索引（RAG 文档嵌入）       | **必须**                |
| `edagent_ollama_data`  | ollama     | `/root/.ollama`            | LLM + Embedding 模型权重 | **必须**（否则重新拉取约 5GB）   |
| `edagent_ingest_data`  | agent-api  | `/data/ingest`             | 用户上传的原始文档            | 推荐                    |
| `edagent_redis_data`   | redis      | `/data`                    | 缓存（非持久业务数据）          | 可选                    |
| `edagent_grafana_data` | grafana    | `/var/lib/grafana`         | 仪表盘配置                | 可选（配置已在 config/ 自动供给） |
| `edagent_prom_data`    | prometheus | `/prom`                    | 历史指标                 | 可选                    |

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

| 症状                   | 原因            | 解决                                                                                             |
| -------------------- | ------------- | ---------------------------------------------------------------------------------------------- |
| PostgreSQL 启动失败      | 数据卷权限或版本不一致   | 删卷重建，用 `pg_dump.sql` 恢复：`docker exec -i edagent-postgres psql -U agent ai_agent < pg_dump.sql` |
| Ollama 模型不可见         | 数据卷恢复路径不对     | `docker exec edagent-ollama ollama list` 确认，无则重新 `ollama pull`                                 |
| ChromaDB 报集合不存在      | 向量索引未恢复       | 重新上传文档到 `/api/v1/rag/documents`                                                                |
| agent-api 启动报错连不上 PG | 启动顺序问题        | `docker compose up -d postgres` 先等 healthy，再 `up -d agent-api`                                 |
| Nginx 502            | agent-api 未就绪 | 等 30 秒重试，或 `docker logs edagent-agent-api --tail 20`                                           |

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

### 14.6 云端模式部署（VPS + 云端 LLM API）

适用场景：个人使用、服务器无 GPU、不想在本地跑模型。

1. **生成新密钥**（不要沿用本地 `.env` 的值）：PowerShell 示例 `-join ((48..57)+(97..122) | Get-Random -Count 24 | % {[char]$_})`，填入 `.env.cloud`。
2. **填 LLM / Embedding API Key**：模板里默认 DeepSeek（LLM）+ 硅基流动（Embedding）示例；也可全用硅基流动一个 key 通吃（LLM 用 Qwen / DeepSeek 系列）。
3. **项目目录传到 VPS**（不要带本地 `.env`；`.env.cloud` 含密钥，勿提交仓库）。
4. **启动**：`docker compose --env-file .env.cloud -f docker-compose.yml -f docker-compose.cloud.yml up -d`。
5. **安全组只放行 80**（配 TLS 后加 443），其余端口一律不要对外；数据库等用 SSH 隧道访问。

必读注意：

- **换 embedding 模型会改变向量维度，旧知识库向量失效**：compose 已自动换成 `agent_edagent_cloud` collection，首次启动后需重新导入文档。
- **云端是空数据库**：首个注册者自动成为 admin。
- **注册开关默认关闭**（`.env.cloud` 的 `AGENT_ALLOW_REGISTER=false`）：即便关闭注册，库中无用户时仍会放行以引导首个管理员，**注册成功后该入口自动失效**；后续新增账号可在管理后台由 admin 创建/管理。
- 本地容器内 Ollama 与在线 LLM 的切换见 14.3「切换到在线 LLM」，此处不重复。

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

| # | 坑点                                       | 原因                                   | 修复                                                     |
| - | ---------------------------------------- | ------------------------------------ | ------------------------------------------------------ |
| 1 | SSE 报 `NotImplementedError`              | 同步 PostgresSaver 不支持异步图              | 改用 AsyncPostgresSaver                                  |
| 2 | `TypeError: run_sync() unexpected kwarg` | anyio 不透传 kwargs                     | 用 functools.partial 包装                                 |
| 3 | Chroma `exactly one operator`            | 多键 where 不接受平铺                       | 用 `{"$and": [...]}`                                    |
| 4 | 前端永远停在"推理中"                              | reactive() 对象误用 .value               | reactive 直接访问属性，不用 .value                              |
| 5 | embedding 400 "invalid input type"       | tokenizer 长度检查不兼容                    | OpenAIEmbeddings 设 check\_embedding\_ctx\_length=False |
| 6 | Rollup 构建报 "not exported"                | @element-plus/icons-vue 无 Calculator | 改用 DataAnalysis                                        |
| 7 | Grafana 13000 页面 404                     | 浏览器 Vite Service Worker 残留           | 无痕窗口或清除 SW                                             |
| 8 | PowerShell 命令被拦                          | ExecutionPolicy Restricted           | 拆成单条简单命令                                               |

## 17. Skills 索引

项目内 `.trae/skills/` 下有 5 个 Trae Skill，在对话中自动匹配加载：

| Skill                    | 触发场景                       |
| ------------------------ | -------------------------- |
| langgraph-agent-builder  | 创建 Agent 图、定义节点和边          |
| rag-pipeline-builder     | 构建 RAG 检索管道                |
| observability-setup      | 配置 Prometheus/Grafana/OTel |
| docker-compose-generator | 生成 Docker Compose 配置       |
| edagent-user-ui-sse      | 用户系统/SSE/UI 排错             |

## 18. 后续扩展方向

前一批扩展已于 2026-09-28 完成：角色系统（admin/user + 管理端点）、DOCX/XLSX 摄取、
会话导出 Markdown、SSE 流式取消（断连检测 + 部分答案落库）、多 Agent 协作子图
（planner → worker → reviewer，路由标签 `team`）。

本批扩展已于 2026-09-28 完成：管理后台页面（前端 `/admin`，用户列表/角色切换/删除）、
会话置顶/搜索（标题关键词模糊匹配 + 置顶排序）、PPTX/CSV 文档摄取（ingest.py 新增
python-pptx + csv 解析）、导出格式扩展（Markdown / HTML / 打印为 PDF）、协作小组
可配置（`AGENT_TEAM_MAX_ROUNDS` 及 planner/reviewer 提示词通过 `.env` 配置）。

Team OS（多角色协作子系统）阶段 1-3 已于 2026-10-13 实现（角色注册表 / DAG 并发调度 /
PR 补丁 / Docker 沙箱 / 多团队隔离 / 通知系统 / 模板库），见第 9.6 节与第 7.1 节。

Phase 3 能力扩展已于 2026-10-15 完成并验证：插件系统（进程内 Python 插件 + `GET/POST/DELETE /api/v1/plugins`）、
批量文档导入（`POST /api/v1/rag/batch` + `ingest_jobs` 表 + 并发限流）、RAG 检索调参
（`rag_search_type` / `rag_score_threshold`）、会话标签分类（tags/category/favorite + 筛选端点）、
WebSocket 实时多用户协作（只读围观 + `share_token`）。

2026-10-19 新增云端部署模式（个人 VPS + 云端 LLM API，`docker-compose.cloud.yml` override + `.env.cloud`，见 6.4 / 14.6 节）。

2026-10-20 新增公网注册开关（`AGENT_ALLOW_REGISTER`，云端默认关闭；库中无用户时放行以引导首个 admin，见 5.1 / 9.1 / 10.2 节）。

下一批候选：

- **模型微调或 RAG 调优**：Ollama Modelfile 自定义微调，或升级到更大参数模型（如 qwen2.5:14b）提升命中率
- **多语言界面（i18n）**：中英文等多语言切换
- **Team OS 产品化**：角色可视化管理页（自定义 system_prompt/工具白名单）、任务 DAG 图形化展示、
  产物版本 diff 对比、通知 Webhook/邮件外发、团队运行成本与耗时统计
- **Team OS 算力升级**：GPU 模式或接入在线 API，才能让 6 角色串行/并发协作具备实用性

