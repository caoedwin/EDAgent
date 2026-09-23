"""Prometheus 指标定义（与 Grafana 仪表板 / 可观测性技能规范对应）。"""

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)

# 单位：秒
REQUEST_DURATION = Histogram(
    "agent_request_duration_seconds",
    "API 请求处理耗时",
    labelnames=("method", "route", "status"),
    buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 120, 300),
)

# model / type=input|output
TOKEN_USAGE = Counter(
    "agent_token_usage_total",
    "LLM Token 使用量",
    labelnames=("model", "type"),
)

# tool / status=success|error
TOOL_CALLS = Counter(
    "agent_tool_call_total",
    "Agent 工具调用次数",
    labelnames=("tool", "status"),
)

ERRORS = Counter(
    "agent_error_total",
    "错误总数",
    labelnames=("route", "type"),
)

# model / operation=chat|embeddings
LLM_INFERENCE_DURATION = Histogram(
    "llm_inference_duration_seconds",
    "LLM 推理耗时",
    labelnames=("model", "operation"),
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 20, 40, 60, 120, 300),
)


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST


def record_tokens(model: str, prompt_tokens: int | None, completion_tokens: int | None) -> None:
    if prompt_tokens:
        TOKEN_USAGE.labels(model=model, type="input").inc(prompt_tokens)
    if completion_tokens:
        TOKEN_USAGE.labels(model=model, type="output").inc(completion_tokens)
