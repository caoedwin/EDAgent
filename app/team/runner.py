"""Team OS 运行器：PM 拆解任务 → DAG 并发调度 → 人工确认暂停。"""

import logging
import uuid
from typing import Any

from app import db
from app.team.nodes import pm_node
from app.team.scheduler import DagScheduler
from app.team.templates import get_template

logger = logging.getLogger(__name__)


class TeamRunner:
    """团队任务执行器（DAG 并发 + 人工确认暂停）。"""

    def __init__(self, pool):
        self.pool = pool

    async def start(
        self,
        *,
        goal: str,
        user_id: int,
        conversation_id: str | None = None,
        template: str | None = None,
        name: str | None = None,
    ) -> str:
        """启动一个团队协作任务，返回 team_id。"""
        team_id = str(uuid.uuid4())
        # 团队模板：解析出允许的角色集合，用于约束 PM 拆解结果
        allowed_roles: set[str] | None = None
        if template:
            tmpl = get_template(template)
            if tmpl is not None:
                allowed_roles = set(tmpl["roles"])

        # 先建团队记录（任务记录在 PM 拆解后写入）
        team_name = (name or "").strip() or goal.strip().replace("\n", " ")[:30] or "未命名团队"
        db.create_team(
            self.pool,
            team_id=team_id,
            user_id=user_id,
            name=team_name,
            template=template,
        )

        state: dict[str, Any] = {
            "team_id": team_id,
            "goal": goal,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "artifacts": {},
            "confirmations": {},
        }
        # 1. PM 拆解任务（按模板角色过滤）
        state = await pm_node(state, self.pool, allowed_roles=allowed_roles)
        # 2. DAG 并发执行
        scheduler = DagScheduler(self.pool)
        await scheduler.run_team(team_id, state)
        # 3. 按终态写入通知
        self._notify_after_run(team_id, user_id)
        return team_id

    async def confirm_task(self, team_id: str, task_id: str, approved: bool) -> dict[str, Any]:
        """用户对任务的人工确认，确认后继续执行后续任务。"""
        state: dict[str, Any] = {
            "team_id": team_id,
            "artifacts": {},
            "confirmations": {task_id: "approved" if approved else "rejected"},
        }
        # 恢复已有的 artifacts
        tasks = db.list_team_tasks(self.pool, team_id)
        user_id = tasks[0]["user_id"] if tasks else None
        for task in tasks:
            if task["artifact_id"]:
                artifact = db.get_artifact(self.pool, task["artifact_id"])
                if artifact:
                    state["artifacts"][task["role"]] = artifact

        if not approved:
            db.update_task_status(self.pool, task_id, "cancelled")
        else:
            db.update_task_status(self.pool, task_id, "pending")

        # 继续执行（DAG 调度器会跳过已完成/失败的任务）
        scheduler = DagScheduler(self.pool)
        await scheduler.run_team(team_id, state)
        if user_id is not None:
            self._notify_after_run(team_id, user_id)
        return {"team_id": team_id, "status": "continued"}

    def _notify_after_run(self, team_id: str, user_id: int) -> None:
        """按团队任务的终态写入通知：任务阻塞 / 任务失败 / 团队完成。"""
        tasks = db.list_team_tasks(self.pool, team_id)
        if not tasks:
            return

        for task in tasks:
            if task["status"] == "blocked":
                db.create_notification(
                    self.pool,
                    user_id=user_id,
                    team_id=team_id,
                    task_id=task["id"],
                    type="task_blocked",
                    title=f"任务待人工确认：{task['title']}",
                    body=task.get("description") or "",
                )
            elif task["status"] == "failed":
                db.create_notification(
                    self.pool,
                    user_id=user_id,
                    team_id=team_id,
                    task_id=task["id"],
                    type="task_failed",
                    title=f"任务执行失败：{task['title']}",
                    body=task.get("error") or "",
                )

        if all(t["status"] in ("done", "failed", "cancelled") for t in tasks):
            summary = {}
            for t in tasks:
                summary[t["status"]] = summary.get(t["status"], 0) + 1
            detail = "、".join(f"{k}: {v}" for k, v in summary.items())
            db.create_notification(
                self.pool,
                user_id=user_id,
                team_id=team_id,
                task_id=None,
                type="team_done",
                title="团队协作已完成",
                body=f"共 {len(tasks)} 个任务：{detail}",
            )
