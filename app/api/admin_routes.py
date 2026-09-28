"""管理员端点：用户管理（列表 / 改角色 / 删除）。

仅 admin 角色可访问（require_admin 以数据库中的角色为准）。
防护规则：
- 不能删除自己 / 修改自己的角色
- 系统中必须始终保留至少一名管理员
- 删除用户会级联删除其会话、消息与 LangGraph 检查点（不可恢复）
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app import db
from app.security.web_auth import require_admin

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

VALID_ROLES = {"admin", "user"}


class RoleUpdate(BaseModel):
    role: str = Field(pattern="^(admin|user)$")


@router.get("/users")
def list_users(request: Request, admin: dict = Depends(require_admin)) -> dict:
    """用户列表（含每人会话数）。"""
    return {"items": db.list_users(request.app.state.pool)}


@router.patch("/users/{user_id}/role")
def change_role(
    user_id: int,
    payload: RoleUpdate,
    request: Request,
    admin: dict = Depends(require_admin),
) -> dict:
    """修改用户角色。禁止修改自己的角色；禁止撤销最后一名管理员。"""
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="不能修改自己的角色")
    if payload.role not in VALID_ROLES:
        raise HTTPException(status_code=422, detail="角色只能是 admin 或 user")

    pool = request.app.state.pool
    target = db.get_user(pool, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if (
        target.get("role") == "admin"
        and payload.role == "user"
        and db.count_admins(pool) <= 1
    ):
        raise HTTPException(status_code=400, detail="系统至少需要保留一名管理员")

    db.set_user_role(pool, user_id, payload.role)
    return {"ok": True, "id": user_id, "role": payload.role}


@router.delete("/users/{user_id}")
def remove_user(user_id: int, request: Request, admin: dict = Depends(require_admin)) -> dict:
    """删除用户及其全部数据。禁止删除自己；禁止删除最后一名管理员。"""
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="不能删除自己的账号")

    pool = request.app.state.pool
    target = db.get_user(pool, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target.get("role") == "admin" and db.count_admins(pool) <= 1:
        raise HTTPException(status_code=400, detail="系统至少需要保留一名管理员")

    deleted = db.delete_user(pool, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"ok": True, "id": user_id, "username": target["username"]}
