"""OpenTelemetry 追踪初始化与 gen_ai 语义约定辅助函数。

- 追踪通过 OTLP gRPC 上报到 otel-collector；endpoint 为空时自动降级为不导出。
- collector 不可用时 BatchSpanProcessor 在后台静默丢弃，不阻塞业务请求。
"""

import contextlib
from collections.abc import Iterator
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Tracer

_initialized = False


def init_tracing(app: Any, endpoint: str, service_name: str) -> None:
    """初始化 TracerProvider 并对 FastAPI 应用做自动埋点。"""
    global _initialized
    if _initialized:
        return

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    if endpoint:
        # insecure=True：容器内网明文 gRPC，与 collector 配置一致
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    _initialized = True


def get_tracer() -> Tracer:
    return trace.get_tracer("edagent")


@contextlib.contextmanager
def gen_ai_span(
    name: str,
    *,
    model: str,
    operation: str,
    system: str = "ollama",
    attributes: dict[str, Any] | None = None,
) -> Iterator[Span]:
    """创建带 gen_ai.* 语义属性的 span（对应可观测性技能规范）。"""
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as span:
        span.set_attribute("gen_ai.system", system)
        span.set_attribute("gen_ai.request.model", model)
        span.set_attribute("gen_ai.operation.name", operation)
        for key, value in (attributes or {}).items():
            if value is not None:
                span.set_attribute(key, value)
        yield span


def set_span_token_usage(span: Span, prompt_tokens: int | None, completion_tokens: int | None) -> None:
    if prompt_tokens is not None:
        span.set_attribute("gen_ai.usage.input_tokens", prompt_tokens)
    if completion_tokens is not None:
        span.set_attribute("gen_ai.usage.output_tokens", completion_tokens)
