#!/usr/bin/env bash
# GPU 检测：nvidia-smi 可用且 Docker GPU 直通验证成功输出 gpu，否则输出 cpu
set -euo pipefail

if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  if docker run --rm --gpus all nvidia/cuda:12.6.1-base-ubuntu24.04 nvidia-smi >/dev/null 2>&1; then
    echo "gpu"
    exit 0
  fi
fi

echo "cpu"
