# syntax=docker/dockerfile:1.7
# EdAgent 应用镜像（Node 构建 Vue3 SPA + Python 3.14-slim / uv 多阶段构建）

# ---------- 阶段 1：前端 SPA ----------
FROM node:22-alpine AS frontend
WORKDIR /fe
# 国内构建走 npmmirror，先单独安装依赖以利用层缓存
RUN npm config set registry https://registry.npmmirror.com
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- 阶段 2：Python 依赖 ----------
FROM python:3.14-slim AS builder
# 不依赖 ghcr.io（国内网络常不可达）：直接从 PyPI 安装 uv
RUN pip install --no-cache-dir uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app

# 先单独解析依赖（利用层缓存；仓库未提交 uv.lock 时在构建期生成）
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv lock && \
    uv sync --no-install-project --no-dev

# 再拷业务代码
COPY app ./app

# ---------- 阶段 3：运行时 ----------
FROM python:3.14-slim AS runtime
RUN groupadd -r agent && useradd -r -g agent agent
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/app /app/app
# Vue3 构建产物（FastAPI 静态托管）
COPY --from=frontend /fe/dist /app/static
# 预建 ingest 目录并归属 agent：named volume 首次挂载会继承该属主
RUN mkdir -p /data/ingest && chown -R agent:agent /data/ingest
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER agent
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"
# 单 worker：PostgresSaver 复用单一连接，且 SSE 长连接与 CPU 推理场景无需多进程
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
