# AI-Agent-Linux.md

# 本地化 AI Agent 生产环境开发文档（Linux）

> 目标：本地化优先、Docker 部署、生产级健壮、自动兼容 NVIDIA GPU / 纯 CPU、支持本地 Ollama 与在线 OpenAI 兼容 API。  
> 平台：Ubuntu 22.04 / 24.04 LTS  
> Python：3.14  
> 用途：交付给 Codex / Trae 作为开发规范与实施指南。

---

## 一、核心设计

### 1. GPU / CPU 自动兼容

不通过 Python 判断，启动时由 Shell 脚本检测：

- 如果 `nvidia-smi` 可用，且 Docker GPU 直通成功，使用 `docker-compose.gpu.yml`。
- 否则自动使用 `docker-compose.cpu.yml`。

### 2. 本地模型 / 在线 API 自动兼容

统一使用 OpenAI 兼容协议：

- 本地 Ollama：`LLM_BASE_URL=http://ollama:11434/v1`
- 在线 API：`LLM_BASE_URL=https://api.deepseek.com/v1` 或 OpenAI / OpenRouter / Qwen 等
- 业务代码只使用 `ChatOpenAI` / `OpenAIEmbeddings`
- 通过 `.env` 切换 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`

无需在 Python 中写 `if provider == ...`。

---

## 二、目录结构

```text
project/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── llm/
│   │   └── client.py
│   ├── agents/
│   ├── rag/
│   ├── memory/
│   ├── observability/
│   └── security/
├── config/
│   ├── nginx.conf
│   ├── otel-collector.yaml
│   └── prometheus.yml
├── scripts/
│   ├── detect-gpu.sh
│   └── up.sh
├── secrets/
│   └── pg_password.txt
├── docker-compose.yml
├── docker-compose.gpu.yml
├── docker-compose.cpu.yml
├── Dockerfile
├── pyproject.toml
├── uv.lock
├── .env
└── .env.example
```

---

## 三、环境变量 `.env.example`

```env
# PostgreSQL
PG_PASSWORD=change_me

# API 网关
AGENT_API_KEY=change_me

# Grafana
GRAFANA_PASSWORD=change_me

# ========== LLM ==========
# 本地 Ollama 模式
LLM_BASE_URL=http://ollama:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=llama3.1:8b

# 在线 API 示例：DeepSeek
# LLM_BASE_URL=https://api.deepseek.com/v1
# LLM_API_KEY=sk-xxx
# LLM_MODEL=deepseek-chat

# 在线 API 示例：OpenAI
# LLM_BASE_URL=https://api.openai.com/v1
# LLM_API_KEY=sk-xxx
# LLM_MODEL=gpt-4o-mini

# ========== Embedding ==========
# 本地 Ollama
EMBEDDING_BASE_URL=http://ollama:11434/v1
EMBEDDING_API_KEY=ollama
EMBEDDING_MODEL=nomic-embed-text

# 在线 Embedding 示例
# EMBEDDING_BASE_URL=https://api.openai.com/v1
# EMBEDDING_API_KEY=sk-xxx
# EMBEDDING_MODEL=text-embedding-3-small

# CPU 推理线程数
OLLAMA_NUM_THREADS=8
```

---

## 四、GPU 自动检测与启动

### `scripts/detect-gpu.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  if docker run --rm --gpus all nvidia/cuda:12.6.1-base-ubuntu24.04 nvidia-smi >/dev/null 2>&1; then
    echo "gpu"
    exit 0
  fi
fi

echo "cpu"
```

### `scripts/up.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

MODE="$(./scripts/detect-gpu.sh)"
COMPOSE_FILES="-f docker-compose.yml"

if [ "$MODE" = "gpu" ]; then
  echo "检测到 GPU，使用 GPU 模式启动"
  COMPOSE_FILES="$COMPOSE_FILES -f docker-compose.gpu.yml"
else
  echo "未检测到可用 GPU，使用 CPU 模式启动"
  COMPOSE_FILES="$COMPOSE_FILES -f docker-compose.cpu.yml"
fi

docker compose $COMPOSE_FILES up -d
```

赋予执行权限：

```bash
chmod +x scripts/detect-gpu.sh scripts/up.sh
```

---

## 五、Docker Compose

### `docker-compose.yml`

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
      OLLAMA_KEEP_ALIVE: "24h"
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
      context: .
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
      AGENT_LLM_BASE_URL: ${LLM_BASE_URL:-http://ollama:11434/v1}
      AGENT_LLM_API_KEY: ${LLM_API_KEY:-ollama}
      AGENT_LLM_MODEL: ${LLM_MODEL:-llama3.1:8b}

      AGENT_EMBEDDING_BASE_URL: ${EMBEDDING_BASE_URL:-http://ollama:11434/v1}
      AGENT_EMBEDDING_API_KEY: ${EMBEDDING_API_KEY:-ollama}
      AGENT_EMBEDDING_MODEL: ${EMBEDDING_MODEL:-nomic-embed-text}

      AGENT_DATABASE_URL: postgresql://agent:${PG_PASSWORD}@postgres:5432/ai_agent
      AGENT_REDIS_URL: redis://redis:6379
      AGENT_OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector:4317
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

### `docker-compose.gpu.yml`

```yaml
services:
  ollama:
    environment:
      NVIDIA_VISIBLE_DEVICES: all
      OLLAMA_NUM_PARALLEL: "4"
      OLLAMA_MAX_LOADED_MODELS: "2"
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

### `docker-compose.cpu.yml`

```yaml
services:
  ollama:
    environment:
      OLLAMA_NUM_PARALLEL: "1"
      OLLAMA_NUM_THREADS: "${OLLAMA_NUM_THREADS:-8}"
      OLLAMA_MAX_LOADED_MODELS: "1"
```

---

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

---

## 七、OTel Collector `config/otel-collector.yaml`

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

---

## 八、Prometheus `config/prometheus.yml`

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

---

## 九、Dockerfile

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

---

## 十、统一 LLM 调用：本地 / 在线 API 不判断

### `app/config.py`

```python
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENT_",
        env_file=".env",
        extra="ignore",
    )

    llm_base_url: str = "http://ollama:11434/v1"
    llm_api_key: SecretStr = SecretStr("ollama")
    llm_model: str = "llama3.1:8b"

    embedding_base_url: str = "http://ollama:11434/v1"
    embedding_api_key: SecretStr = SecretStr("ollama")
    embedding_model: str = "nomic-embed-text"

    llm_temperature: float = 0.1
    llm_max_tokens: int = 2048
    llm_timeout: int = 120
    llm_max_retries: int = 2

    database_url: str
    redis_url: str
    otel_exporter_otlp_endpoint: str
    api_key: SecretStr


settings = Settings()
```

### `app/llm/client.py`

```python
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.config import settings


def get_chat_model() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        timeout=settings.llm_timeout,
        max_retries=settings.llm_max_retries,
    )


def get_embedding_model() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.embedding_api_key,
        base_url=settings.embedding_base_url,
    )
```

依赖：

```toml
langchain-openai = "*"
```

---

## 十一、Linux 安装

### 1. 安装 Docker Engine

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker $USER
```

重新登录后验证：

```bash
docker version
docker compose version
```

### 2. 如果有 NVIDIA GPU

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

验证：

```bash
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.6.1-base-ubuntu24.04 nvidia-smi
```

### 3. 启动

```bash
cp .env.example .env
chmod +x scripts/*.sh
./scripts/up.sh
```

---

## 十二、验收标准

- `./scripts/detect-gpu.sh` 输出 `gpu` 或 `cpu`。
- 有 GPU 时：`docker exec -it ollama nvidia-smi` 有输出。
- 无 GPU 时：Ollama 自动使用 CPU。
- `./scripts/up.sh` 一键启动。
- 所有服务健康检查通过。
- `LLM_BASE_URL=http://ollama:11434/v1` 时，本地模型可返回结果。
- `LLM_BASE_URL=https://api.deepseek.com/v1` 时，在线 API 可返回结果。
- RAG 可基于本地文档回答。
- Jaeger 可见追踪。
- Prometheus / Grafana 可见指标。
- Windows / 客户端可通过 `localhost:8080` 访问 API。

---

## 十三、Linux 排障

| 问题 | 解决 |
|---|---|
| GPU 容器无输出 | 安装 NVIDIA Container Toolkit，重启 Docker |
| Ollama 回退 CPU | 显存不足，换更小模型，或增大 `OLLAMA_KEEP_ALIVE` |
| Docker 权限不足 | `sudo usermod -aG docker $USER` 后重新登录 |
| 在线 API 超时 | 检查 `LLM_BASE_URL`、代理、防火墙 |
| Redis 版本问题 | 固定 `redis:7.2-alpine` |
| Compose GPU 不生效 | 确认 `docker compose` 版本支持 `deploy.resources.reservations.devices` |