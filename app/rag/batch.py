"""批量文档导入：后台异步执行，限制并发避免 CPU 过载。"""

import asyncio
import logging
from typing import Any

from psycopg_pool import ConnectionPool

from app import db
from app.rag.ingest import ingest_document

logger = logging.getLogger(__name__)

# embedding 是 CPU 密集操作，限制并发避免打满
_SEMAPHORE = asyncio.Semaphore(2)


async def _ingest_single_file(
    user_id: int,
    filename: str,
    content: bytes,
    results: list[dict[str, Any]],
    index: int,
) -> None:
    """摄取单个文件，结果写入共享 results 列表。"""
    async with _SEMAPHORE:
        try:
            stat = await asyncio.to_thread(
                ingest_document, filename, content, user_id=f"user-{user_id}"
            )
            results[index] = {
                "filename": filename,
                "status": "success",
                "chunks": stat["chunks"],
                "pages": stat["pages"],
            }
        except Exception as exc:
            logger.warning("批量导入文件失败 %s: %s", filename, exc)
            results[index] = {
                "filename": filename,
                "status": "failed",
                "error": str(exc)[:500],
            }


async def run_batch_ingest(
    pool: ConnectionPool,
    job_id: str,
    user_id: int,
    files: list[tuple[str, bytes]],
) -> None:
    """后台批量摄取入口（由 API 层 asyncio.create_task 调用）。"""
    results: list[dict[str, Any]] = [{}] * len(files)

    tasks = [
        _ingest_single_file(user_id, fn, content, results, i)
        for i, (fn, content) in enumerate(files)
    ]
    await asyncio.gather(*tasks, return_exceptions=True)

    completed = sum(1 for r in results if r.get("status") == "success")
    failed = sum(1 for r in results if r.get("status") == "failed")
    final_status = "done" if failed == 0 else ("failed" if completed == 0 else "done")

    db.update_ingest_job(
        pool,
        job_id,
        status=final_status,
        completed_files=completed,
        failed_files=failed,
        results=results,
    )
    logger.info("批量导入完成 job=%s 成功=%d 失败=%d", job_id, completed, failed)
