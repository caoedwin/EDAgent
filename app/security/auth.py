"""API Key 鉴权（Bearer Token）。

/health、/metrics、文档等路径在中间件/依赖层白名单放行。
"""

import hmac

from fastapi import Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings

# 无需鉴权的路径前缀/精确路径
PUBLIC_PATHS = frozenset({"/health", "/metrics", "/docs", "/redoc", "/openapi.json"})

_bearer = HTTPBearer(auto_error=False)


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS


async def require_api_key(request: Request) -> str:
    """FastAPI 依赖：校验 Authorization: Bearer <AGENT_API_KEY>。"""
    if is_public(request.url.path):
        return "anonymous"

    credentials: HTTPAuthorizationCredentials | None = await _bearer(request)
    expected = settings.api_key.get_secret_value()
    if credentials is None or not hmac.compare_digest(credentials.credentials, expected):
        raise StarletteHTTPException(status_code=401, detail="Missing or invalid API key")
    return credentials.credentials
