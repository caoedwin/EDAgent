# AI-Agent-Linux.md

# 本地化 AI Agent 生产环境开发文档（Linux）

> 目标：完全免费、本地化、Docker 部署、生产级健壮的 AI Agent。\
> 平台：Ubuntu 22.04 / 24.04 LTS\
> Python：3.14\
> 硬件：无 GPU，纯 CPU 推理\
> 用途：交付给 Codex / Trae 作为开发规范与实施指南。

***

## 一、项目总览

### 1.1 核心能力

- 本地 LLM 推理：Ollama CPU 模式
- RAG 知识检索：ChromaDB
- Agent 状态编排：LangGraph
- 持久化记忆：PostgreSQL + Redis + ChromaDB
- 可观测性：OpenTelemetry + Jaeger + Prometheus + Grafana
- 安全网关：Nginx + TLS + API Key + 限流

### 1.2 硬件最低要求（无 GPU）

| 资源  | 最低               | 推荐               |
| :-- | :--------------- | :--------------- |
| CPU | 8 核，支持 AVX2      | 16 核+，支持 AVX-512 |
| RAM | 32GB             | 64GB+            |
| 磁盘  | 100GB SSD        | 256GB+ NVMe      |
| OS  | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS |

> CPU 推理速度约为 GPU 的 1/10 \~ 1/30。建议使用 7B 及以下模型，如 `llama3.1:8b`、`qwen2.5:7b`。

***

## 二、工具清单与版本

### 2.1 基础设施

| 工具             | 版本       | 许可证        | 作用    |
| :------------- | :------- | :--------- | :---- |
| Docker Engine  | 24.0+    | Apache 2.0 | 容器运行时 |
| Docker Compose | v2.38.1+ | Apache 2.0 | 多容器编排 |
| Git            | 2.40+    | GPLv2      | 版本控制  |

### 2.2 模型推理

| 工具               | 版本        | 许可证        | 作用           |
| :--------------- | :-------- | :--------- | :----------- |
| Ollama           | 0.32.3 固定 | MIT        | 本地 LLM 运行时   |
| nomic-embed-text | 最新        | Apache 2.0 | 本地 Embedding |

### 2.3 Agent 编排

| 工具        | 版本    | 许可证 | 作用            |
| :-------- | :---- | :-- | :------------ |
| LangGraph | 最新稳定版 | MIT | 状态机式 Agent 编排 |
| LangChain | 最新稳定版 | MIT | LLM 调用、工具集成   |

### 2.4 数据持久层

| 工具         | 版本         | 许可证                | 作用       |
| :--------- | :--------- | :----------------- | :------- |
| PostgreSQL | 16-alpine  | PostgreSQL License | 主数据库     |
| Redis      | 7.2-alpine | BSD                | 缓存、会话、限流 |
| ChromaDB   | 1.5.9      | Apache 2.0         | 向量数据库    |

> Redis 必须使用 7.2.x，7.4+ 已改为 RSALv2/SSPLv1，不再是传统开源。

### 2.5 网关与安全

| 工具             | 版本           | 许可证          | 作用          |
| :------------- | :----------- | :----------- | :---------- |
| Nginx          | nginx:alpine | 2-clause BSD | 反向代理、TLS 终止 |
| Docker Secrets | 内置           | Apache 2.0   | 敏感信息管理      |

### 2.6 可观测性

| 工具                      | 版本    | 许可证        | 作用    |
| :---------------------- | :---- | :--------- | :---- |
| OpenTelemetry Collector | 最新稳定版 | Apache 2.0 | 统一采集  |
| Jaeger                  | v2    | Apache 2.0 | 分布式追踪 |
| Prometheus              | 最新稳定版 | Apache 2.0 | 指标采集  |
| Grafana OSS             | 最新稳定版 | AGPLv3     | 可视化   |

### 2.7 开发工具链

| 工具      | 版本    | 许可证            | 作用       |
| :------ | :---- | :------------- | :------- |
| Python  | 3.14  | PSF            | 编程语言     |
| uv      | 最新版   | MIT/Apache 2.0 | 包管理      |
| FastAPI | 最新稳定版 | MIT            | API 服务框架 |

***

## 三、系统架构

```mermaid
flowchart TB
    U[用户/客户端] -->|HTTPS/SSE| NG[Nginx :443<br/>TLS终止 + 限流 + SSE透传]
    NG --> API[FastAPI :8080<br/>请求验证 + OTel埋点]
    API --> LG[LangGraph 编排层<br/>Router → ReAct → Tool → Report]
    LG --> OL[Ollama :11434<br/>CPU LLM 推理 + Embedding]
    LG --> PG[(PostgreSQL :5432<br/>业务数据 + 检查点)]
    LG --> RD[(Redis :6379<br/>缓存 + 会话 + 限流)]
    LG --> CH[(ChromaDB :8000<br/>向量存储 + RAG)]
    API -.-> OT[OTel Collector]
    OT --> JG[Jaeger]
    OT --> PR[Prometheus]
    PR --> GF[Grafana]
```

### 请求流程

1. 用户请求 → Nginx：TLS 终止、限流、API Key 验证。
2. FastAPI：解析请求，创建 OpenTelemetry Span。
3. LangGraph：初始化状态，Router 判断任务类型。
4. 简单问答：直接调用 Ollama。
5. 复杂任务：ReAct 循环 → 工具调用 → 检查点 → 循环。
6. RAG：ChromaDB 语义检索，增强上下文。
7. 报告生成：结构化输出返回。

> CPU 推理建议：`OLLAMA_NUM_THREADS` 设为物理核心数，`OLLAMA_MAX_LOADED_MODELS=1`。

***

## 四、Docker Compose（Linux）

```yaml
version: "3.8"

services:
  ollama:
    image: ollama/ollama:0.32.3
    container_name: ollama
    ports:
      - "127.0.0.1:11434:11434"
    volumes:
      - ollama_data:/root/.ollama
    environment:
      - OLLAMA_KEEP_ALIVE=24h
      - OLLAMA_NUM_PARALLEL=1
      - OLLAMA_NUM_THREADS=8
      - OLLAMA_MAX_LOADED_MODELS=1
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:11434/api/tags"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
    restart: unless-stopped

  chromadb:
    image: chromadb/chroma:1.5.9
    container_name: chromadb
    ports:
      - "127.0.0.1:8000:8000"
    volumes:
      - chroma_data:/chroma/chroma
    environment:
      IS_PERSISTENT: "TRUE"
      PERSIST_DIRECTORY: "/chroma/chroma"
      ANONYMIZED_TELEMETRY: "FALSE"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/heartbeat"]
      interval: 15s
      timeout: 5s
      retries: 3
    deploy:
      resources:
        limits:
          memory: 2G
    restart: unless-stopped

  postgres:
    image: postgres:16-alpine
    container_name: postgres
    ports:
      - "127.0.0.1:5432:5432"
    volumes:
      - pg_data:/var/lib/postgresql/data
    environment:
      POSTGRES_DB: ai_agent
      POSTGRES_USER: agent
      POSTGRES_PASSWORD_FILE: /run/secrets/pg_password
    secrets:
      - pg_password
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U agent -d ai_agent"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  redis:
    image: redis:7.2-alpine
    container_name: redis
    ports:
      - "127.0.0.1:6379:6379"
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes --maxmemory 512mb --maxmemory-policy allkeys-lru
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 3
    restart: unless-stopped

  agent-api:
    build:
      context: ./app
      dockerfile: Dockerfile
    container_name: agent-api
    depends_on:
      ollama:
        condition: service_healthy
      chromadb:
        condition: service_healthy
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    ports:
      - "127.0.0.1:8080:8080"
    environment:
      OLLAMA_HOST: http://ollama:11434
      CHROMA_HOST: chromadb
      CHROMA_PORT: 8000
      DATABASE_URL: postgresql://agent:${PG_PASSWORD}@postgres:5432/ai_agent
      REDIS_URL: redis://redis:6379
      OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector:4317
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 15s
      timeout: 5s
      retries: 3
    restart: unless-stopped

  otel-collector:
    image: otel/opentelemetry-collector-contrib:latest
    container_name: otel-collector
    volumes:
      - ./config/otel-collector.yaml:/etc/otelcol-contrib/config.yaml
    ports:
      - "127.0.0.1:4317:4317"
      - "127.0.0.1:4318:4318"
    depends_on:
      - jaeger
    restart: unless-stopped

  jaeger:
    image: jaegertracing/jaeger:2.x
    container_name: jaeger
    ports:
      - "127.0.0.1:16686:16686"
    environment:
      - COLLECTOR_OTLP_ENABLED=true
    restart: unless-stopped

volumes:
  ollama_data:
  chroma_data:
  pg_data:
  redis_data:

secrets:
  pg_password:
    file: ./secrets/pg_password.txt
```

***

## 五、Dockerfile（Python 3.14）

```dockerfile
# syntax=docker/dockerfile:1.7
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --locked --no-install-project --no-dev
COPY . .

FROM python:3.14-slim AS runtime
RUN groupadd -r agent && useradd -r -g agent agent
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app /app
ENV PATH="/app/.venv/bin:$PATH"
USER agent
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
```

***

## 六、代码组织

```
project/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── agents/
│   │   ├── graph.py
│   │   ├── router_agent.py
│   │   ├── react_agent.py
│   │   └── tools/
│   ├── rag/
│   │   ├── ingest.py
│   │   ├── retriever.py
│   │   └── chunking.py
│   ├── memory/
│   │   ├── short_term.py
│   │   ├── long_term.py
│   │   └── checkpoint.py
│   ├── observability/
│   │   ├── tracing.py
│   │   └── metrics.py
│   └── security/
│       ├── auth.py
│       ├── sandbox.py
│       └── rate_limit.py
├── config/
│   ├── otel-collector.yaml
│   └── prometheus.yml
├── secrets/
│   └── pg_password.txt
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── uv.lock
└── .env.example
```

***

## 七、编码与生产规则

| 项      | 要求                            |
| :----- | :---------------------------- |
| Python | 3.14                          |
| 包管理    | uv：`uv sync`、`uv lock`        |
| 类型注解   | 所有函数必须包含                      |
| 异步     | I/O 使用 async/await            |
| 配置     | Pydantic Settings，前缀 `AGENT_` |
| 日志     | 结构化 JSON，禁止 print             |
| 密钥     | Docker Secrets / 环境变量         |
| 健康检查   | 所有服务必须配置                      |
| 资源限制   | 所有容器设置内存/CPU 限制               |
| 重启     | `restart: unless-stopped`     |
| 端口     | 仅绑定 `127.0.0.1`               |
| 安全     | 工具沙箱、审计日志、高风险审批               |

### LLM 调用（CPU）

```python
from langchain_ollama import ChatOllama

llm = ChatOllama(
    model="llama3.1:8b",
    base_url="http://ollama:11434",
    temperature=0.1,
    num_predict=2048,
    timeout=300,
)
```

***

## 八、验收标准

- `docker compose up -d` 一键启动
- 所有服务健康检查通过
- Agent 可处理 HTTP 请求并返回 LLM 结果
- RAG 可基于本地文档回答
- LangGraph 支持检查点与断点恢复
- Jaeger 可见完整追踪
- 单次 LLM 调用 < 60s（CPU，7B）
- 支持至少 5 并发

