"""统一 LLM 调用层：本地 Ollama / 在线 API 对业务代码完全透明。

只暴露 ChatOpenAI / OpenAIEmbeddings（OpenAI 兼容协议），
通过 settings 的 base_url / api_key / model 切换后端，代码中不做 provider 判断。
"""

import time
from collections.abc import Sequence
from functools import lru_cache

from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.config import settings
from app.observability.metrics import LLM_INFERENCE_DURATION, record_tokens
from app.observability.tracing import gen_ai_span, set_span_token_usage


@lru_cache(maxsize=4)
def get_chat_model(*, temperature: float | None = None) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=settings.llm_temperature if temperature is None else temperature,
        max_tokens=settings.llm_max_tokens,
        timeout=settings.llm_timeout,
        max_retries=settings.llm_max_retries,
        disable_streaming=False,
    )


@lru_cache(maxsize=1)
def get_embedding_model() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.embedding_api_key,
        base_url=settings.embedding_base_url,
        timeout=settings.llm_timeout,
        max_retries=settings.llm_max_retries,
        check_embedding_ctx_length=False,
    )


def _extract_token_usage(response: BaseMessage) -> tuple[int | None, int | None]:
    usage = (response.response_metadata or {}).get("token_usage") or {}
    return usage.get("prompt_tokens"), usage.get("completion_tokens")


def tracked_invoke(runnable, messages: Sequence[BaseMessage], *, operation: str = "chat") -> BaseMessage:
    """对任意 LangChain runnable（如 bind_tools 后的 LLM）做统一的追踪 / 指标 / Token 统计。"""
    start = time.perf_counter()
    with gen_ai_span(
        "chat llm",
        model=settings.llm_model,
        operation=operation,
    ) as span:
        response = runnable.invoke(list(messages))
        elapsed = time.perf_counter() - start
        prompt_tokens, completion_tokens = _extract_token_usage(response)
        set_span_token_usage(span, prompt_tokens, completion_tokens)

    LLM_INFERENCE_DURATION.labels(model=settings.llm_model, operation=operation).observe(elapsed)
    record_tokens(settings.llm_model, prompt_tokens, completion_tokens)
    return response


def chat_invoke(messages: Sequence[BaseMessage], *, temperature: float | None = None) -> BaseMessage:
    """带追踪 / 指标 / Token 统计的同步对话调用。"""
    return tracked_invoke(get_chat_model(temperature=temperature), messages)


async def tracked_ainvoke(
    runnable, messages: Sequence[BaseMessage], *, operation: str = "chat"
) -> BaseMessage:
    """tracked_invoke 的异步版本。

    必须在 async 图节点中使用：ainvoke 内部基于 astream 消费，
    token 级 on_chat_model_stream 事件才能冒泡到 graph.astream_events。
    """
    start = time.perf_counter()
    with gen_ai_span(
        "chat llm",
        model=settings.llm_model,
        operation=operation,
    ) as span:
        response = await runnable.ainvoke(list(messages))
        elapsed = time.perf_counter() - start
        prompt_tokens, completion_tokens = _extract_token_usage(response)
        set_span_token_usage(span, prompt_tokens, completion_tokens)

    LLM_INFERENCE_DURATION.labels(model=settings.llm_model, operation=operation).observe(elapsed)
    record_tokens(settings.llm_model, prompt_tokens, completion_tokens)
    return response


async def chat_ainvoke(
    messages: Sequence[BaseMessage], *, temperature: float | None = None
) -> BaseMessage:
    """带追踪 / 指标 / Token 统计的异步对话调用（支持上游流式事件）。"""
    return await tracked_ainvoke(get_chat_model(temperature=temperature), messages)


def embed_query(text: str) -> list[float]:
    """单条查询向量化（带追踪）。"""
    embedder = get_embedding_model()
    start = time.perf_counter()
    with gen_ai_span(
        "embeddings",
        model=settings.embedding_model,
        operation="embeddings",
    ):
        vector = embedder.embed_query(text)
    LLM_INFERENCE_DURATION.labels(model=settings.embedding_model, operation="embeddings").observe(
        time.perf_counter() - start
    )
    return vector
