#!/usr/bin/env bash
# 一键启动：自动检测 GPU/CPU 并叠加对应 compose 文件
# 用法：./scripts/up.sh [其他 docker compose up 参数，例如 -d]
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  echo "已从 .env.example 创建 .env，请修改密码后重新运行"
  exit 1
fi

MODE="$(./scripts/detect-gpu.sh)"
COMPOSE_FILES=(-f docker-compose.yml)

if [ "$MODE" = "gpu" ]; then
  echo "检测到 GPU，使用 GPU 模式启动"
  COMPOSE_FILES+=(-f docker-compose.gpu.yml)
else
  echo "未检测到可用 GPU，使用 CPU 模式启动"
  COMPOSE_FILES+=(-f docker-compose.cpu.yml)
fi

docker compose "${COMPOSE_FILES[@]}" up -d --build "$@"

cat <<'TIP'
------------------------------------------------------------
EdAgent 已启动（首次启动 Ollama 会在后台拉取模型）：
  API 入口(Nginx): http://localhost:18088
  API 直连:       http://localhost:18080
  Jaeger UI:      http://localhost:16686
  Prometheus:     http://localhost:19090
  Grafana:        http://localhost:13000
调用业务接口需带请求头：Authorization: Bearer <AGENT_API_KEY>
TIP
