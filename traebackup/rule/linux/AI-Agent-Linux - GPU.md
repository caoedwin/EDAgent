# AI-Agent-Linux-GPU.md

# 本地化 AI Agent 生产环境开发文档（Linux + GPU）

> 目标：完全免费、本地化、Docker 部署、生产级健壮、支持 NVIDIA GPU 的 AI Agent。\
> 平台：Ubuntu 22.04 / 24.04 LTS\
> Python：3.14\
> 推理：Ollama GPU 加速\
> 用途：交付给 Codex / Trae 作为开发规范与实施指南。

***

## 一、硬件与 GPU 前置

| 项目  | 最低              | 推荐                |
| :-- | :-------------- | :---------------- |
| CPU | 8 核             | 16 核+             |
| RAM | 32GB            | 64GB+             |
| GPU | NVIDIA 8GB VRAM | NVIDIA 16GB+ VRAM |
| 磁盘  | 100GB SSD       | 256GB+ NVMe       |
| 驱动  | NVIDIA 535+     | 最新生产驱动            |

### 安装 NVIDIA Container Toolkit

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### 验证

```bash
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.6.1-base-ubuntu24.04 nvidia-smi
```

> 不要通过 snap 安装 Docker，会与 NVIDIA Container Toolkit 冲突。

***

## 二、工具清单与版本

| 工具                       | 版本           | 许可证                | 作用           |
| :----------------------- | :----------- | :----------------- | :----------- |
| Docker Engine            | 24.0+        | Apache 2.0         | 容器运行时        |
| Docker Compose           | v2.38.1+     | Apache 2.0         | 多容器编排        |
| NVIDIA Container Toolkit | 最新           | Apache 2.0         | GPU 直通       |
| Ollama                   | 0.32.3 固定    | MIT                | 本地 LLM 推理    |
| nomic-embed-text         | 最新           | Apache 2.0         | 本地 Embedding |
| LangGraph                | 最新稳定         | MIT                | Agent 状态编排   |
| LangChain                | 最新稳定         | MIT                | LLM 调用与工具集成  |
| PostgreSQL               | 16-alpine    | PostgreSQL License | 主数据库         |
| Redis                    | 7.2-alpine   | BSD                | 缓存、会话、限流     |
| ChromaDB                 | 1.5.9        | Apache 2.0         | 向量数据库        |
| Nginx                    | nginx:alpine | 2-clause BSD       | 反向代理、TLS、SSE |
| OpenTelemetry Collector  | 最新稳定         | Apache 2.0         | 统一采集         |
| Jaeger                   | v2 稳定        | Apache 2.0         | 分布式追踪        |
| Prometheus               | 最新稳定         | Apache 2.0         | 指标采集         |
| Grafana OSS              | 最新稳定         | AGPLv3             | 可视化          |
| Python                   | 3.14         | PSF                | 编程语言         |
| uv                       | 最新           | MIT/Apache 2.0     | 包管理          |
| FastAPI                  | 最新稳定         | MIT                | API 框架       |

> Redis 必须 7.2.x，7.4+ 已改为 RSALv2/SSPLv1。

***

## 三、系统架构

```mermaid
flowchart TB
    U[用户/客户端] -->|HTTPS/SSE| NG[Nginx<br/>TLS + 限流 + SSE]
    NG --> API[FastAPI :8080]
    API --> LG[LangGraph 编排层]
    LG --> OL[Ollama GPU :11434]
    LG --> PG[(PostgreSQL :5432)]
    LG --> RD[(Redis :6379)]
    LG --> CH[(ChromaDB :8000)]
    API -.-> OT[OTel Collector]
    OT --> JG[Jaeger]
    OT --> PR[Prometheus]
    PR --> GF[Grafana]
```

### 请求流程

1. 用户请求 → Nginx：TLS、限流、API Key。
2. FastAPI：解析请求，创建 OpenTelemetry Span。
3. LangGraph：初始化状态，Router 判断任务。
4. 简单问答 → Ollama GPU。
5. 复杂任务 → ReAct 循环 → 工具调用 → 检查点 → 循环。
6. RAG → ChromaDB 语义检索。
7. 结构化输出返回。

***

## 四、目录结构

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
│   ├── nginx.conf
│   ├── otel-collector.yaml
│   └── prometheus.yml
├── secrets/
│   └── pg_password.txt
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── uv.lock
└── .env
```

***

## 五、Docker Compose（Linux + GPU 完整）

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
      - OLLAMA_NUM_PARALLEL=4
      - OLLAMA_MAX_LOADED_MODELS=2
      - NVIDIA_VISIBLE_DEVICES=all
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    healthcheck:
      test: ["CMD", "ollama", "list"]
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
      AGENT_API_KEY: ${AGENT_API_KEY}
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"]
      interval: 15s
      timeout: 5s
      retries: 3
    restart: unless-stopped

  nginx:
    image: nginx:alpine
    container_name: nginx
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./config/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./certs:/etc/nginx/certs:ro
    depends_on:
      - agent-api
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
    image: jaegertracing/jaeger:2.6.0
    container_name: jaeger
    ports:
      - "127.0.0.1:16686:16686"
    environment:
      - COLLECTOR_OTLP_ENABLED=true
    restart: unless-stopped

  prometheus:
    image: prom/prometheus:latest
    container_name: prometheus
    volumes:
      - ./config/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prom_data:/prometheus
    ports:
      - "127.0.0.1:9090:9090"
    restart: unless-stopped

  grafana:
    image: grafana/grafana-oss:latest
    container_name: grafana
    ports:
      - "127.0.0.1:3000:3000"
    volumes:
      - grafana_data:/var/lib/grafana
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_PASSWORD}
    depends_on:
      - prometheus
    restart: unless-stopped

volumes:
  ollama_data:
  chroma_data:
  pg_data:
  redis_data:
  prom_data:
  grafana_data:

secrets:
  pg_password:
    file: ./secrets/pg_password.txt
```

`.env` 示例：

```env
PG_PASSWORD=change_me
AGENT_API_KEY=change_me
GRAFANA_PASSWORD=change_me
```

***

## 六、Nginx 配置 `config/nginx.conf`

```nginx
events {}

http {
  upstream agent_backend {
    server agent-api:8080;
  }

  server {
    listen 80;
    server_name _;

    location / {
      proxy_pass http://agent_backend;
      proxy_http_version 1.1;
      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header Connection "";
      proxy_buffering off;
      proxy_cache off;
      proxy_read_timeout 300s;
    }
  }
}
```

> 生产 TLS 可挂载证书到 `/etc/nginx/certs`，并增加 443 server 块。

***

## 七、OTel Collector 配置 `config/otel-collector.yaml`

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

exporters:
  otlp/jaeger:
    endpoint: jaeger:4317
    tls:
      insecure: true
  prometheus:
    endpoint: 0.0.0.0:8889

service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [otlp/jaeger]
    metrics:
      receivers: [otlp]
      exporters: [prometheus]
```

***

## 八、Prometheus 配置 `config/prometheus.yml`

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: otel
    static_configs:
      - targets: ["otel-collector:8889"]

  - job_name: agent-api
    static_configs:
      - targets: ["agent-api:8080"]
```

***

## 九、Dockerfile（Python 3.14）

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

## 十、开发与生产规则

| 项      | 要求                           |
| :----- | :--------------------------- |
| Python | 3.14                         |
| 包管理    | uv                           |
| 类型注解   | 所有函数                         |
| 异步     | I/O 使用 async/await           |
| 配置     | Pydantic Settings，前缀 AGENT\_ |
| 日志     | 结构化 JSON，禁止 print            |
| 密钥     | Docker Secrets / 环境变量        |
| 健康检查   | 所有服务必须配置                     |
| 资源限制   | 所有容器设置内存/CPU                 |
| 重启     | restart: unless-stopped      |
| 端口     | 内部仅绑定 127.0.0.1，Nginx 对外     |
| 安全     | 工具沙箱、审计日志、高风险审批              |

### LLM 调用

```python
from langchain_ollama import ChatOllama

llm = ChatOllama(
    model="llama3.1:8b",
    base_url="http://ollama:11434",
    temperature=0.1,
    num_predict=2048,
    timeout=120,
)
```

***

## 十一、显存与模型参考

| 模型规模    | 文件大小    | 推荐显存    | 示例          |
| :------ | :------ | :------ | :---------- |
| 3B-4B   | 2-3GB   | 4-6GB   | llama3.2:3b |
| 7B-8B   | 4.9GB   | 8-10GB  | llama3.1:8b |
| 14B     | 9GB     | 12-16GB | qwen2.5:14b |
| 27B-32B | 17-19GB | 24-32GB | qwen3.6:27b |
| 70B     | 47-52GB | 48-80GB | 多卡          |

***

## 十二、验收标准

- `docker compose up -d` 一键启动
- `docker exec -it ollama nvidia-smi` 有输出
- 所有服务健康检查通过
- Agent 可处理 HTTP 请求并返回 LLM 结果
- RAG 可基于本地文档回答
- LangGraph 支持检查点与断点恢复
- Jaeger 可见完整追踪
- Prometheus / Grafana 可见 GPU 与 Token 指标
- 单次 LLM 调用 < 30s（GPU，7B）

***

## 十三、GPU 排障

| 问题                             | 原因                           | 解决                          |
| :----------------------------- | :--------------------------- | :-------------------------- |
| could not select device driver | 未安装 NVIDIA Container Toolkit | 安装并配置 runtime               |
| 容器内 nvidia-smi 无输出             | 驱动或 Docker 未重启               | 重启 Docker                   |
| Ollama 回退 CPU                  | 显存不足或 keep\_alive 太短         | 减小模型，增大 OLLAMA\_KEEP\_ALIVE |
| Secure Boot 驱动失败               | DKMS 未签名                     | mokutil --import 后重启        |

