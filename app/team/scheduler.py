"""DAG 并发调度器：按任务依赖关系拓扑排序，无依赖的任务并发执行。

用法：
    scheduler = DagScheduler(pool, max_parallel=2)
    await scheduler.run_team(team_id, state)
"""

import asyncio
import logging
from typing import Any

from app import db
from app.config import settings
from app.team.nodes import role_executor_node

logger = logging.getLogger(__name__)


class DagScheduler:
    """基于任务 DAG 的并发调度器。"""

    def __init__(self, pool, *, max_parallel: int | None = None):
        self.pool = pool
        self.max_parallel = max_parallel or settings.team_max_parallel
        self._semaphore = asyncio.Semaphore(self.max_parallel)

    async def run_team(self, team_id: str, state: dict[str, Any]) -> dict[str, Any]:
        """执行团队所有任务（遵循 DAG 依赖 + 并发上限）。"""
        tasks = db.list_team_tasks(self.pool, team_id)
        state["tasks"] = tasks

        # 构建 id -> task 映射
        task_map = {t["id"]: t for t in tasks}
        # 按 depends_on 构建依赖图
        dependents: dict[str, list[str]] = {t["id"]: [] for t in tasks}
        for t in tasks:
            for dep in t.get("depends_on", []):
                # dep 可能是 PM 输出的 t1/t2 逻辑编号，需匹配
                dep_task = self._find_by_logical_id(tasks, dep)
                if dep_task:
                    dependents[dep_task["id"]].append(t["id"])

        # 已完成的任务 id 集合
        completed: set[str] = set()
        for t in tasks:
            if t["status"] in ("done", "failed", "cancelled"):
                completed.add(t["id"])

        # 待执行任务
        pending = {t["id"] for t in tasks if t["status"] not in ("done", "failed", "cancelled")}
        blocked_tasks: set[str] = set()  # 需人工确认而阻塞的

        running: dict[str, asyncio.Task] = {}

        while pending or running:
            # 找出所有依赖已满足且未在执行的任务
            ready: list[str] = []
            for tid in list(pending):
                task = task_map[tid]
                deps = self._resolve_deps(task, tasks)
                if all(d in completed for d in deps):
                    ready.append(tid)

            # 启动 ready 任务
            for tid in ready:
                pending.discard(tid)
                task = task_map[tid]
                # 检查是否需要人工确认
                role = db.get_role(self.pool, task["role"])
                if role and role["requires_human_confirm"]:
                    confirmation = state.get("confirmations", {}).get(tid)
                    if confirmation != "approved":
                        db.update_task_status(self.pool, tid, "blocked")
                        blocked_tasks.add(tid)
                        continue

                coro = self._execute_task(tid, task, state, task_map)
                running[tid] = asyncio.create_task(coro)

            # 等待至少一个任务完成
            if running:
                done, _ = await asyncio.wait(
                    running.values(), return_when=asyncio.FIRST_COMPLETED
                )
                for finished in done:
                    # 找到对应的 tid
                    tid = next(k for k, v in running.items() if v is finished)
                    running.pop(tid)
                    try:
                        result = finished.result()
                        # 合并结果到 state
                        if "artifacts" in result:
                            state["artifacts"].update(result["artifacts"])
                    except Exception:
                        logger.exception("task %s failed", tid)

                    # 刷新 DB 状态
                    updated = db.list_team_tasks(self.pool, team_id)
                    current = next((t for t in updated if t["id"] == tid), None)
                    if current:
                        task_map[tid] = current
                        if current["status"] in ("done", "failed", "cancelled"):
                            completed.add(tid)
                        elif current["status"] == "blocked":
                            blocked_tasks.add(tid)
            elif pending and not ready:
                # 既无运行中任务、也无就绪任务：本次调用无法再推进。
                # 两种情况都必须立即返回，否则该分支没有 await，会空转把事件循环卡死：
                #   1) 有任务被人工确认阻塞 → 等用户 confirm 后再由 confirm_task 继续调度；
                #   2) 无阻塞任务但依赖始终不满足 → 疑似循环依赖。
                if not blocked_tasks:
                    logger.warning("可能的循环依赖，剩余任务: %s", pending)
                break

        return {
            "completed": list(completed),
            "blocked": list(blocked_tasks),
            "total": len(tasks),
        }

    async def _execute_task(
        self,
        tid: str,
        task: dict[str, Any],
        state: dict[str, Any],
        task_map: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """执行单个任务（带并发限制信号量）。"""
        async with self._semaphore:
            # 构建子 state
            sub_state = {
                **state,
                "tasks": list(task_map.values()),
                "current_task_index": next(
                    i for i, t in enumerate(task_map.values()) if t["id"] == tid
                ),
            }
            return await role_executor_node(sub_state, self.pool)

    def _resolve_deps(self, task: dict[str, Any], all_tasks: list[dict[str, Any]]) -> list[str]:
        """将任务的 depends_on（可能是逻辑编号 t1/t2）解析为实际 task id。"""
        deps: list[str] = []
        for dep in task.get("depends_on", []):
            resolved = self._find_by_logical_id(all_tasks, dep)
            if resolved:
                deps.append(resolved["id"])
            # 也可能是直接 UUID
            elif dep in [t["id"] for t in all_tasks]:
                deps.append(dep)
        return deps

    @staticmethod
    def _find_by_logical_id(tasks: list[dict[str, Any]], logical_id: str) -> dict[str, Any] | None:
        """PM 输出 t1/t2 等逻辑编号，按创建顺序匹配。"""
        # 简单映射：t1 → 第 1 个任务，t2 → 第 2 个...
        match = __import__("re").match(r"t(\d+)", logical_id.strip().lower())
        if match:
            idx = int(match.group(1)) - 1
            if 0 <= idx < len(tasks):
                return tasks[idx]
        return None
