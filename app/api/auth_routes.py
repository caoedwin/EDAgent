"""用户注册 / 登录 / 登出 / 当前用户（Web UI 会话 Cookie 鉴权）。"""

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app import db
from app.security.web_auth import (
    SESSION_ROLE_KEY,
    SESSION_USERNAME_KEY,
    SESSION_USER_KEY,
    require_login,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5]{3,32}$")


class AuthRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6, max_length=64)


def _validate_credentials_format(username: str, password: str) -> None:
    if not _USERNAME_PATTERN.match(username):
        raise HTTPException(
            status_code=422,
            detail="用户名需为 3-32 位，可包含字母、数字、下划线或中文",
        )
    if len(password) < 6:
        raise HTTPException(status_code=422, detail="密码至少 6 位")


@router.post("/register")
def register(payload: AuthRequest, request: Request) -> dict:
    _validate_credentials_format(payload.username, payload.password)
    try:
        user = db.create_user(request.app.state.pool, payload.username, payload.password)
    except db.UsernameTaken as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # 注册成功直接建立会话，前端无需再跳登录
    request.session[SESSION_USER_KEY] = user["id"]
    request.session[SESSION_USERNAME_KEY] = user["username"]
    request.session[SESSION_ROLE_KEY] = user.get("role", "user")
    return {"id": user["id"], "username": user["username"], "role": user.get("role", "user")}


@router.post("/login")
def login(payload: AuthRequest, request: Request) -> dict:
    try:
        user = db.authenticate(request.app.state.pool, payload.username, payload.password)
    except db.InvalidCredentials as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    request.session[SESSION_USER_KEY] = user["id"]
    request.session[SESSION_USERNAME_KEY] = user["username"]
    request.session[SESSION_ROLE_KEY] = user.get("role", "user")
    return {"id": user["id"], "username": user["username"], "role": user.get("role", "user")}


@router.post("/logout")
def logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(require_login)) -> dict:
    return {"id": user["id"], "username": user["username"], "role": user.get("role", "user")}
