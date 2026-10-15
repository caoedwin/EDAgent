"""插件管理端点：仅 admin 可访问。"""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.plugins.loader import delete_plugin_file, get_plugin_info, save_plugin_file
from app.security.web_auth import require_admin

router = APIRouter(prefix="/api/v1/plugins", tags=["plugins"])

_MAX_PLUGIN_BYTES = 1024 * 1024  # 1MB 上限


class PluginResponse(BaseModel):
    filename: str
    name: str
    size_bytes: int


@router.get("")
def list_plugins(admin: dict = Depends(require_admin)) -> dict:
    """列出当前已安装的插件文件。"""
    return {"items": get_plugin_info()}


@router.post("")
async def upload_plugin(
    file: UploadFile = File(...),
    admin: dict = Depends(require_admin),
) -> dict:
    """上传插件 .py 文件。上传后需重启 agent-api 才能生效。"""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(content) > _MAX_PLUGIN_BYTES:
        raise HTTPException(status_code=413, detail="插件文件超过 1MB 上限")
    try:
        path = save_plugin_file(file.filename or "plugin.py", content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "filename": path.name,
        "name": path.stem,
        "message": "插件已保存，重启 agent-api 后生效",
    }


@router.delete("/{name}")
def remove_plugin(name: str, admin: dict = Depends(require_admin)) -> dict:
    """删除插件文件。删除后需重启 agent-api 才能生效。"""
    if not delete_plugin_file(name):
        raise HTTPException(status_code=404, detail="插件不存在")
    return {"ok": True, "name": name, "message": "插件已删除，重启 agent-api 后生效"}
