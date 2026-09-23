# AI-Agent-Windows-WSL2-GPU.md

# 本地化 AI Agent 生产环境开发文档（Windows / WSL2 + GPU）

> 目标：完全免费、本地化、Docker 部署、生产级健壮、支持 NVIDIA GPU 的 AI Agent。\
> 平台：Windows Server 2022+ 或 Windows 10/11，通过 WSL2\
> Python：3.14\
> 推理：Ollama GPU 加速\
> 用途：交付给 Codex / Trae 作为开发规范与实施指南。

***

## 一、Windows 平台关键约束

### 1. 生产推荐：WSL2 + Docker Engine

| 方案                          | 场景    | 说明                                     |
| :-------------------------- | :---- | :------------------------------------- |
| WSL2 + Docker Engine        | 生产推荐  | 在 WSL2 Ubuntu 中安装原生 Docker Engine，完全免费 |
| Hyper-V 虚拟机 + Docker Engine | 备选    | 完整 Linux VM，隔离好                        |
| Docker Desktop              | 开发/测试 | 有商业许可限制，大企业需付费                         |

> Windows Server 不支持 Docker Desktop。生产必须在 WSL2 或 Hyper-V 中运行 Docker Engine。

### 2. GPU 支持前提

GPU 直通仅在使用 WSL2 后端时可用。需要：

- Windows 10/11 或 Windows Server 2022+
- 支持 WSL2 GPU Paravirtualization 的 NVIDIA 驱动
- 最新 WSL2 内核
- Docker Desktop 或 WSL2 内 Docker Engine

***

## 二、WSL2 前置配置

PowerShell（管理员）：

```powershell
wsl --install
wsl --set-default-version 2
wsl --install -d Ubuntu-24.04
wsl --update
```

进入 WSL2 后安装 Docker Engine：

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker $USER
```

### 验证 GPU

在 WSL2 或 PowerShell 中：

```bash
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.6.1-base-ubuntu24.04 nvidia-smi
```

如果第二条输出 GPU 信息，则 GPU 直通成功。

> 如果使用 Docker Desktop，确保 Settings → General 勾选 “Use the WSL 2 based engine”。

***

## 三、工具清单与版本

与 Linux 版一致：

| 工具                      | 版本           | 许可证                | 作用        |
| :---------------------- | :----------- | :----------------- | :-------- |
| Docker Engine           | 24.0+        | Apache 2.0         | 容器运行时     |
| Docker Compose          | v2.38.1+     | Apache 2.0         | 多容器编排     |
| NVIDIA 驱动               | WSL2 支持版     | NVIDIA             | GPU 直通    |
| Ollama                  | 0.32.3 固定    | MIT                | 本地 LLM 推理 |
| nomic-embed-text        | 最新           | Apache 2.0         | Embedding |
| LangGraph               | 最新稳定         | MIT                | Agent 编排  |
| LangChain               | 最新稳定         | MIT                | LLM 与工具   |
| PostgreSQL              | 16-alpine    | PostgreSQL License | 主数据库      |
| Redis                   | 7.2-alpine   | BSD                | 缓存、会话、限流  |
| ChromaDB                | 1.5.9        | Apache 2.0         | 向量数据库     |
| Nginx                   | nginx:alpine | 2-clause BSD       | 反向代理、SSE  |
| OpenTelemetry Collector | 最新稳定         | Apache 2.0         | 统一采集      |
| Jaeger                  | v2 稳定        | Apache 2.0         | 分布式追踪     |
| Prometheus              | 最新稳定         | Apache 2.0         | 指标采集      |
| Grafana OSS             | 最新稳定         | AGPLv3             | 可视化       |
| Python                  | 3.14         | PSF                | 编程语言      |
| uv                      | 最新           | MIT/Apache 2.0     | 包管理       |
| FastAPI                 | 最新稳定         | MIT                | API 框架    |

***

## 四、系统架构

```mermaid
flowchart TB
    subgraph WinHost["Windows Server / Windows 10/11"]
        subgraph WSL2["WSL2 Ubuntu 24.04"]
            subgraph Docker["Docker Engine"]
                NG[Nginx]
                API[FastAPI Agent]
                OL[Ollama GPU]
                PG[(PostgreSQL)]
                RD[(Redis)]
                CH[(ChromaDB)]
                OT[OTel Collector]
                JG[Jaeger]
                PR[Prometheus]
                GF[Grafana]
            end
        end
    end
    U[用户/客户端] -->|HTTPS/SSE| NG
    NG --> API
    API --> OL
    API --> PG
    API --> RD
    API --> CH
    API -.-> OT
    OT --> JG
    OT --> PR
    PR --> GF
```

> 所有容器运行在 WSL2 Linux 内核中，Compose 与 Linux 版基本一致。

***

## 五、目录结构

```
project/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── agents/
│   ├── rag/
│   ├── memory/
│   ├── observability/
│   └── security/
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

## 六、Docker Compose（Windows/WSL2 + GPU 完整）

在 WSL2 的 Linux 项目目录中创建 `docker-compose.yml`：

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

> 如果 Ollama 运行在 Windows 宿主机而不是 WSL2 容器中，删除 `ollama` 服务，并把 `agent-api` 的 `OLLAMA_HOST` 改为 `http://host.docker.internal:11434`。

***

## 七、Nginx 配置 `config/nginx.conf`

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

***

## 八、OTel Collector 配置 `config/otel-collector.yaml`

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

## 九、Prometheus 配置 `config/prometheus.yml`

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

## 十、Dockerfile（Python 3.14）

与 Linux 版完全相同：

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

## 十一、Windows/WSL2 特有注意事项

| 事项      | 要求                                           |
| :------ | :------------------------------------------- |
| 文件路径    | 在 WSL2 内使用 `/home/user/project`，避免 `/mnt/c/` |
| 换行符     | `git config --global core.autocrlf input`    |
| 文件监听    | 开发时使用轮询模式                                    |
| 端口转发    | WSL2 自动转发 localhost 到 Windows                |
| 防火墙     | 允许 WSL2 网络流量                                 |
| WSL2 内存 | 配置 `.wslconfig`                              |
| 数据卷     | 使用 Docker 命名卷，不要放 Windows 文件系统               |

`.wslconfig` 示例，放在 `C:\Users\<用户名>\.wslconfig`：

```ini
[wsl2]
memory=48GB
processors=16
swap=8GB
localhostForwarding=true
```

***

## 十二、开发与生产规则

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
| 端口     | 内部 127.0.0.1，Nginx 对外        |
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

## 十三、显存与模型参考

| 模型规模    | 文件大小    | 推荐显存    | 示例          |
| :------ | :------ | :------ | :---------- |
| 3B-4B   | 2-3GB   | 4-6GB   | llama3.2:3b |
| 7B-8B   | 4.9GB   | 8-10GB  | llama3.1:8b |
| 14B     | 9GB     | 12-16GB | qwen2.5:14b |
| 27B-32B | 17-19GB | 24-32GB | qwen3.6:27b |
| 70B     | 47-52GB | 48-80GB | 多卡          |

***

## 十四、验收标准

- WSL2 正常启动，Docker Engine 运行正常
- `docker run --rm --gpus all nvidia/cuda:12.6.1-base-ubuntu24.04 nvidia-smi` 成功
- `docker compose up -d` 一键启动
- `docker exec -it ollama nvidia-smi` 有输出
- 所有服务健康检查通过
- Agent 可处理 HTTP 请求并返回 LLM 结果
- RAG 可基于本地文档回答
- Jaeger 可见完整追踪
- Windows 宿主机可通过 `localhost:8080` 访问 API
- 单次 LLM 调用 < 30s（GPU，7B）

***

## 十五、Windows GPU 排障

| 问题            | 原因                | 解决                        |
| :------------ | :---------------- | :------------------------ |
| 容器无法访问 Ollama | Ollama 在宿主机       | 使用 `host.docker.internal` |
| GPU 不可用       | WSL2 内核旧          | `wsl --update`            |
| Docker 无响应    | Docker Desktop 干扰 | 使用 WSL2 内 Docker Engine   |
| 卷挂载失败         | Windows 路径格式      | 用 WSL2 内部路径或命名卷           |
| WSL2 内存溢出     | 未限制内存             | 配置 `.wslconfig`           |
| 文件监听不生效       | WSL2 文件事件         | 使用轮询                      |
| Git 换行符       | CRLF/LF 混用        | `core.autocrlf=input`     |

