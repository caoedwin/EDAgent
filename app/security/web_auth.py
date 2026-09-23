"""Web UI 会话鉴权辅助。

两种访问方式并存：
- 浏览器 UI：登录后由 SessionMiddleware 写入签名 Cookie（request.session["user_id"]）
- 程序化调用：Authorization: Bearer <AGENT_API_KEY>（原有机制，不做用户隔离）
"""

import hmac

from fastapi import HTTPException, Request

from app.config import settings

SESSION_USER_KEY = "user_id"
SESSION_USERNAME_KEY = "username"


def session_user_id(request: Request) -> int | None:
    """从登录 Cookie 中解析用户 ID；未登录返回 None。"""
    try:
        raw = request.session.get(SESSION_USER_KEY)
        return int(raw) if raw else None
    except (AttributeError, TypeError, ValueError):
        return None


def has_valid_api_key(request: Request) -> bool:
    """校验 Bearer API Key（程序化访问）。"""
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    expected = settings.api_key.get_secret_value()
    return scheme.lower() == "bearer" and bool(token) and hmac.compare_digest(token, expected)


def request_owner_id(request: Request) -> str | None:
    """当前请求的数据归属：登录用户用 'user-<id>'，API Key 调用返回 None（全局）。"""
    uid = session_user_id(request)
    return f"user-{uid}" if uid is not None else None


async def require_login(request: Request) -> dict:
    """FastAPI 依赖：UI 专属端点必须登录。"""
    uid = session_user_id(request)
    if uid is None:
        raise HTTPException(status_code=401, detail="请先登录")
    user = None
    pool = getattr(request.app.state, "pool", None)
    if pool is not None:
        from app import db

        user = db.get_user(pool, uid)
    if user is None:
        request.session.clear()
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    return user
