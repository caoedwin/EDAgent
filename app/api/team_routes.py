"""Team OS API：启动团队任务 / 查询进度 / 获取产物 / 人工确认。"""

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

from app import db
from app.security.web_auth import require_login
from app.team.patch import apply_diff
from app.team.roles import sync_roles_to_db
from app.team.runner import TeamRunner
from app.team.templates import TEMPLATES, get_template

router = APIRouter(prefix="/api/v1/team", tags=["team"])

# 后台运行中的 team 任务：team_id -> asyncio.Task
_running_tasks: dict[str, asyncio.Task] = {}


class StartTeamRequest(BaseModel):
    goal: str
    conversation_id: str | None = None
    template: str | None = None


class CreateTeamRequest(BaseModel):
    name: str = ""
    goal: str = ""
    conversation_id: str | None = None
    template: str | None = None


class RenameTeamRequest(BaseModel):
    name: str


class ConfirmRequest(BaseModel):
    approved: bool


def _validate_template(template: str | None) -> None:
    """校验团队模板；不存在时抛 400。"""
    if template and get_template(template) is None:
        raise HTTPException(status_code=400, detail=f"团队模板不存在: {template}")


@router.get("/roles")
def list_roles(request: Request) -> dict:
    """列出所有可用角色。"""
    roles = sync_roles_to_db(request.app.state.pool)
    return {"items": roles}


@router.post("/start")
async def start_team(
    req: StartTeamRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_login),
) -> dict:
    """启动一个团队协作任务（PM 拆解 + 各角色顺序执行）。"""
    if not req.goal.strip():
        raise HTTPException(status_code=400, detail="目标不能为空")
    _validate_template(req.template)

    runner = TeamRunner(request.app.state.pool)
    team_id = await runner.start(
        goal=req.goal,
        user_id=user["id"],
        conversation_id=req.conversation_id,
        template=req.template,
    )
    return {"team_id": team_id, "status": "started"}


# ==================== 团队模板 / 我的团队 / 通知 ====================
# 说明：以下固定路径路由必须注册在 "/{team_id}" 之前，
# 否则 "/list"、"/templates"、"/notifications" 会被 "/{team_id}" 吞掉。


@router.get("/templates")
def list_templates(user: dict = Depends(require_login)) -> dict:
    """列出内置团队模板。"""
    return {"items": TEMPLATES}


@router.get("/list")
def list_my_teams(request: Request, user: dict = Depends(require_login)) -> dict:
    """列出当前用户的团队。"""
    return {"items": db.list_teams(request.app.state.pool, user["id"])}


@router.post("/create")
async def create_team(
    req: CreateTeamRequest,
    request: Request,
    user: dict = Depends(require_login),
) -> dict:
    """创建团队；body 带 goal 时立即启动，否则只建团队记录。"""
    _validate_template(req.template)

    if not req.goal.strip():
        # 只建团队，不启动
        team_id = str(uuid.uuid4())
        name = (req.name or "").strip() or "未命名团队"
        db.create_team(
            request.app.state.pool,
            team_id=team_id,
            user_id=user["id"],
            name=name,
            template=req.template,
        )
        return {"team_id": team_id, "status": "created"}

    runner = TeamRunner(request.app.state.pool)
    team_id = await runner.start(
        goal=req.goal,
        user_id=user["id"],
        conversation_id=req.conversation_id,
        template=req.template,
        name=req.name or None,
    )
    return {"team_id": team_id, "status": "started"}


@router.get("/notifications")
def list_notifications(
    request: Request,
    unread_only: bool = False,
    limit: int = 50,
    user: dict = Depends(require_login),
) -> dict:
    """列出当前用户的通知。"""
    items = db.list_notifications(
        request.app.state.pool, user["id"], unread_only=unread_only, limit=limit
    )
    return {"items": items}


@router.get("/notifications/unread-count")
def unread_count(request: Request, user: dict = Depends(require_login)) -> dict:
    """未读通知数量。"""
    return {"count": db.count_unread_notifications(request.app.state.pool, user["id"])}


@router.post("/notifications/read-all")
def read_all_notifications(request: Request, user: dict = Depends(require_login)) -> dict:
    """把当前用户所有通知标记为已读。"""
    changed = db.mark_all_notifications_read(request.app.state.pool, user["id"])
    return {"success": True, "updated": changed}


@router.post("/notifications/{notification_id}/read")
def read_notification(
    notification_id: int,
    request: Request,
    user: dict = Depends(require_login),
) -> dict:
    """标记单条通知为已读。"""
    ok = db.mark_notification_read(request.app.state.pool, notification_id, user["id"])
    return {"success": ok}


@router.get("/{team_id}")
def get_team(team_id: str, request: Request, user: dict = Depends(require_login)) -> dict:
    """查询团队任务进度：任务列表 + 状态。"""
    record = db.get_team_record(request.app.state.pool, team_id)
    tasks = db.list_team_tasks(request.app.state.pool, team_id)
    if record is None and not tasks:
        raise HTTPException(status_code=404, detail="团队任务不存在")
    owner_id = record["user_id"] if record else tasks[0]["user_id"]
    if owner_id != user["id"]:
        raise HTTPException(status_code=403, detail="无权访问")

    status_counts: dict[str, int] = {}
    for task in tasks:
        status_counts[task["status"]] = status_counts.get(task["status"], 0) + 1

    return {
        "team_id": team_id,
        "name": record["name"] if record else None,
        "template": record["template"] if record else None,
        "tasks": tasks,
        "status_summary": status_counts,
        "all_done": all(t["status"] in ("done", "failed", "cancelled") for t in tasks),
    }


@router.patch("/{team_id}/rename")
def rename_team(
    team_id: str,
    req: RenameTeamRequest,
    request: Request,
    user: dict = Depends(require_login),
) -> dict:
    """重命名团队。"""
    record = db.get_team_record(request.app.state.pool, team_id)
    if record is None:
        raise HTTPException(status_code=404, detail="团队不存在")
    if record["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="无权访问")

    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="团队名称不能为空")
    db.rename_team(request.app.state.pool, team_id, name)
    return {"team_id": team_id, "name": name[:100]}


@router.delete("/{team_id}")
def delete_team(
    team_id: str,
    request: Request,
    user: dict = Depends(require_login),
) -> dict:
    """删除团队及其任务 / 产物 / PR。"""
    record = db.get_team_record(request.app.state.pool, team_id)
    if record is None:
        raise HTTPException(status_code=404, detail="团队不存在")
    if record["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="无权访问")

    db.delete_team(request.app.state.pool, team_id)
    return {"success": True}


@router.get("/{team_id}/artifacts")
def list_artifacts(team_id: str, request: Request, user: dict = Depends(require_login)) -> dict:
    """获取团队所有产物。"""
    tasks = db.list_team_tasks(request.app.state.pool, team_id)
    if not tasks:
        raise HTTPException(status_code=404, detail="团队任务不存在")
    if tasks[0]["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="无权访问")

    artifacts = db.list_artifacts_by_team(request.app.state.pool, team_id)
    return {"items": artifacts}


@router.post("/{team_id}/tasks/{task_id}/confirm")
async def confirm_task(
    team_id: str,
    task_id: str,
    req: ConfirmRequest,
    request: Request,
    user: dict = Depends(require_login),
) -> dict:
    """人工确认/拒绝任务（用于 requires_human_confirm 的角色）。"""
    tasks = db.list_team_tasks(request.app.state.pool, team_id)
    if not tasks:
        raise HTTPException(status_code=404, detail="团队任务不存在")
    if tasks[0]["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="无权访问")

    runner = TeamRunner(request.app.state.pool)
    result = await runner.confirm_task(team_id, task_id, req.approved)
    return result


# ==================== PR / 补丁 ====================


@router.get("/{team_id}/prs")
def list_prs(team_id: str, request: Request, user: dict = Depends(require_login)) -> dict:
    """列出团队的所有 PR/补丁。"""
    tasks = db.list_team_tasks(request.app.state.pool, team_id)
    if not tasks:
        raise HTTPException(status_code=404, detail="团队任务不存在")
    if tasks[0]["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="无权访问")
    prs = db.list_prs(request.app.state.pool, team_id)
    return {"items": prs}


class ApplyPrRequest(BaseModel):
    target_dir: str | None = None  # 应用 diff 的目标目录，默认为项目根


@router.post("/{team_id}/prs/{pr_id}/apply")
def apply_pr(
    team_id: str,
    pr_id: str,
    req: ApplyPrRequest,
    request: Request,
    user: dict = Depends(require_login),
) -> dict:
    """应用 PR 补丁到目标目录。"""
    tasks = db.list_team_tasks(request.app.state.pool, team_id)
    if not tasks:
        raise HTTPException(status_code=404, detail="团队任务不存在")
    if tasks[0]["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="无权访问")

    pr = db.get_pr(request.app.state.pool, pr_id)
    if pr is None:
        raise HTTPException(status_code=404, detail="PR 不存在")
    if pr["status"] not in ("pending", "approved"):
        raise HTTPException(status_code=400, detail=f"PR 状态为 {pr['status']}，无法应用")

    import os
    target = req.target_dir or os.getcwd()
    result = apply_diff(pr["diff_content"], target)
    if result["success"]:
        db.update_pr_status(request.app.state.pool, pr_id, "applied")
        return {"success": True, "applied_files": result["applied_files"]}
    else:
        raise HTTPException(status_code=422, detail=result.get("error", "应用失败"))


class RejectPrRequest(BaseModel):
    reason: str = ""


@router.post("/{team_id}/prs/{pr_id}/reject")
def reject_pr(
    team_id: str,
    pr_id: str,
    req: RejectPrRequest,
    request: Request,
    user: dict = Depends(require_login),
) -> dict:
    """拒绝 PR 补丁。"""
    tasks = db.list_team_tasks(request.app.state.pool, team_id)
    if not tasks:
        raise HTTPException(status_code=404, detail="团队任务不存在")
    if tasks[0]["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="无权访问")

    db.update_pr_status(request.app.state.pool, pr_id, "rejected", reviewer_notes=req.reason)
    return {"success": True, "status": "rejected"}
