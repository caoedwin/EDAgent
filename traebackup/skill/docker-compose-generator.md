---
name: docker-compose-generator
description: 根据用户的平台（Linux/Windows/WSL2）和硬件环境（GPU/CPU）自动生成完整的 Docker Compose 配置。当用户需要创建、修改或调试 AI Agent 的 Docker 部署配置时使用。
---

# Docker Compose 生成器

## 参数确认清单

在生成配置前，必须确认以下信息：
1. 目标平台：Linux / Windows(WSL2)
2. GPU：有（型号、显存大小）/ 无
3. 模型规模：3B / 7B / 14B / 27B / 70B
4. 是否需要可观测性（OTel + Jaeger + Prometheus + Grafana）

## 服务清单

必须包含的服务（基础版）：
- ollama：LLM 推理，固定版本 0.32.3
- chromadb：向量数据库，固定版本 1.5.9
- postgres：主数据库，16-alpine
- redis：缓存，7.2-alpine
- agent-api：Agent 应用，自定义构建
- nginx：反向代理

可选服务（可观测性）：
- otel-collector、jaeger、prometheus、grafana

## GPU 配置差异

CPU 模式：
  environment:
    - OLLAMA_NUM_THREADS=<物理核心数>
    - OLLAMA_MAX_LOADED_MODELS=1
    - OLLAMA_NUM_PARALLEL=1

GPU 模式：
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: all
            capabilities: [gpu]
  environment:
    - OLLAMA_NUM_PARALLEL=4
    - OLLAMA_MAX_LOADED_MODELS=2
    - NVIDIA_VISIBLE_DEVICES=all

## 强制规则

- 所有端口仅绑定 127.0.0.1
- 所有服务必须配置 healthcheck 和 restart: unless-stopped
- 敏感信息使用 Docker Secrets
- 镜像标签禁止使用 latest