"""EdAgent FastAPI 入口。

- 启动时初始化 PostgreSQL（业务表 + LangGraph 检查点）并编译 Agent
- 双通道鉴权：Web 登录 Cookie（SessionMiddleware）/ Bearer API Key（程序化调用）
- 托管 Vue3 SPA 静态产物（history 路由 fallback 到 index.html）
- 统一请求耗时 / 错误指标；OpenTelemetry 自动埋点
"""

import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from langchain_core.exceptions import LangChainException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app import __version__, db
from app.agents.graph import build_agent_graph
from app.api.auth_routes import router as auth_router
from app.api.routes import router as api_router
from app.api.ui_routes import router as ui_router
from app.config import settings
from app.memory.checkpointer import create_checkpointer
from app.observability.metrics import ERRORS, REQUEST_DURATION, render_metrics
from app.observability.tracing import init_tracing
from app.security.web_auth import has_valid_api_key, session_user_id

# 无需登录即可访问的 API（注册 / 登录）
PUBLIC_API_PATHS = frozenset({"/api/v1/auth/register", "/api/v1/auth/login"})
STATIC_DIR = Path(settings.static_dir)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Postgres 未就绪时两个工厂内部都会重试
    app.state.pool = db.create_pool(settings.database_url)
    checkpointer = await create_checkpointer()
    app.state.graph = build_agent_graph(checkpointer)
    app.state.checkpointer = checkpointer
    yield
    app.state.pool.close()


app = FastAPI(
    title="EdAgent",
    version=__version__,
    description="本地化 AI Agent：OpenAI 兼容 LLM（本地 Ollama / 在线 API）+ LangGraph + RAG",
    lifespan=lifespan,
)


class SecurityMetricsMiddleware(BaseHTTPMiddleware):
    """鉴权（登录 Cookie 或 Bearer API Key）+ Prometheus 请求耗时/错误统计。"""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        needs_auth = path.startswith("/api/") and path not in PUBLIC_API_PATHS
        if needs_auth:
            authenticated = session_user_id(request) is not None or has_valid_api_key(request)
            if not authenticated:
                return JSONResponse(
                    status_code=401, content={"detail": "未登录或 API Key 无效"}
                )

        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            ERRORS.labels(route=path, type="unhandled_exception").inc()
            raise
        finally:
            elapsed = time.perf_counter() - start
            route_path = getattr(request.scope.get("route"), "path", path)
            REQUEST_DURATION.labels(
                method=request.method,
                route=route_path,
                status=str(status_code),
            ).observe(elapsed)


def custom_openapi():
    """注入 Bearer API Key 安全方案，使 Swagger UI 显示 Authorize 按钮。

    Web UI 端点（/api/v1/ui、/api/v1/auth）使用登录 Cookie，浏览器自动携带；
    Bearer 方案仅服务于程序化 API 调用。
    """
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    openapi_schema.setdefault("components", {})["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "API Key",
            "description": "输入 .env 中的 AGENT_API_KEY（只需填写 key 本身）。",
        }
    }
    openapi_schema["security"] = [{"BearerAuth": []}]
    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi

# 中间件洋葱顺序（后 add 更靠外）：OTel -> Session -> Security -> 路由
app.add_middleware(SecurityMetricsMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret.get_secret_value(),
    session_cookie="edagent_session",
    max_age=settings.session_max_age_seconds,
    same_site="lax",
    https_only=False,
)

# OTel 自动埋点（最外层）；endpoint 为空时仅本地生成 span 不导出
init_tracing(app, settings.otel_exporter_otlp_endpoint, settings.otel_service_name)

app.include_router(api_router)
app.include_router(auth_router)
app.include_router(ui_router)


@app.exception_handler(LangChainException)
async def langchain_exception_handler(request: Request, exc: LangChainException):
    ERRORS.labels(route=request.url.path, type="llm_error").inc()
    return JSONResponse(
        status_code=502,
        content={"detail": f"LLM 服务调用失败（模型可能仍在拉取中）: {exc}"},
    )


@app.get("/health", tags=["system"])
def health() -> dict:
    """轻量健康检查（容器 HEALTHCHECK / Nginx / Prometheus 用）。"""
    return {
        "status": "ok",
        "service": "edagent-api",
        "version": __version__,
        "env": settings.env,
        "llm_model": settings.llm_model,
        "embedding_model": settings.embedding_model,
    }


@app.get("/metrics", tags=["system"])
def metrics() -> Response:
    """Prometheus 指标端点。"""
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)


# ---------------- Vue3 SPA 托管（构建产物存在时启用）----------------
if STATIC_DIR.is_dir():
    assets_dir = STATIC_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        # API / 系统路径的未匹配请求正常返回 404，不回退到前端页面
        if full_path.startswith(("api/", "health", "metrics")):
            raise HTTPException(status_code=404)
        # 命中真实静态文件（favicon、logo 等）直接返回
        if full_path:
            candidate = STATIC_DIR / full_path
            if candidate.is_file():
                return FileResponse(candidate)
        # 其余路径（/、/login、/chat 等 history 路由）统一回退 index.html
        return FileResponse(STATIC_DIR / "index.html")


if __name__ == "__main__":  # 本地直跑：python -m app.main
    uvicorn.run("app.main:app", host="0.0.0.0", port=8080, reload=False)
