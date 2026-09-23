"""LangGraph 检查点：PostgreSQL 异步持久化，支持从任意检查点恢复执行。

使用 AsyncPostgresSaver：图通过 astream_events / ainvoke 异步驱动，
要求 psycopg AsyncConnection 开启 autocommit（LangGraph 官方要求）。
启动时若 Postgres 尚未就绪会自动重试。
"""

import asyncio

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.config import settings


async def create_checkpointer(
    *,
    retries: int = 10,
    interval_seconds: float = 3.0,
) -> AsyncPostgresSaver:
    """建立到 PostgreSQL 的异步检查点连接并执行建表（幂等）。"""
    last_error: Exception | None = None
    for _attempt in range(1, retries + 1):
        try:
            connection = await psycopg.AsyncConnection.connect(
                settings.database_url,
                autocommit=True,
                connect_timeout=10,
                application_name="edagent-checkpointer",
            )
            saver = AsyncPostgresSaver(connection)
            await saver.setup()  # CREATE TABLE IF NOT EXISTS checkpoints / writes / migrations
            return saver
        except Exception as exc:  # 容器并行启动时 PG 可能还没准备好
            last_error = exc
            await asyncio.sleep(interval_seconds)
    raise RuntimeError(f"无法连接 PostgreSQL 检查点存储（已重试 {retries} 次）: {last_error}")
