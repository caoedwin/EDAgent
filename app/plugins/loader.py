"""插件加载器：扫描 plugins/ 目录，导入 @tool 装饰的函数。"""

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("edagent.plugins")

# 插件目录：容器内 /app/plugins，本地开发为项目根下 plugins/
_PLUGIN_DIR = Path(__file__).resolve().parent.parent.parent / "plugins"


def _validate_plugin_module(module: Any, module_name: str) -> list:
    """校验插件模块，返回其中所有 @tool 装饰的函数。"""
    tools = []
    for attr_name in dir(module):
        if attr_name.startswith("_"):
            continue
        attr = getattr(module, attr_name)
        # langchain_core.tools.tool 装饰后返回 StructuredTool（有 .name/.invoke，但不可调用）
        if hasattr(attr, "name") and hasattr(attr, "invoke"):
            tools.append(attr)
            logger.info("插件 %s 注册工具: %s", module_name, attr.name)
    return tools


def load_plugins() -> list:
    """扫描 PLUGIN_DIR 下的 .py 文件，导入并收集工具。"""
    if not _PLUGIN_DIR.is_dir():
        logger.info("插件目录不存在: %s，跳过插件加载", _PLUGIN_DIR)
        return []

    all_tools = []
    for py_file in sorted(_PLUGIN_DIR.glob("*.py")):
        if py_file.name.startswith("_"):
            continue  # 跳过 _ 开头的辅助文件
        module_name = f"edagent_plugin_{py_file.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec is None or spec.loader is None:
                logger.warning("无法加载插件: %s", py_file)
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            tools = _validate_plugin_module(module, py_file.stem)
            all_tools.extend(tools)
        except Exception as exc:
            logger.error("插件加载失败 %s: %s", py_file, exc)
            # 单个插件失败不影响整体启动
    return all_tools


def get_plugin_info() -> list[dict]:
    """返回插件文件列表（用于管理 API）。"""
    if not _PLUGIN_DIR.is_dir():
        return []
    items = []
    for py_file in sorted(_PLUGIN_DIR.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        items.append({
            "filename": py_file.name,
            "name": py_file.stem,
            "size_bytes": py_file.stat().st_size,
        })
    return items


def save_plugin_file(filename: str, content: bytes) -> Path:
    """保存上传的插件文件，返回落盘路径。"""
    safe_name = Path(filename).name
    if not safe_name.endswith(".py"):
        raise ValueError("仅支持 .py 文件")
    _PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    target = _PLUGIN_DIR / safe_name
    target.write_bytes(content)
    return target


def delete_plugin_file(name: str) -> bool:
    """按模块名删除插件文件。"""
    target = _PLUGIN_DIR / f"{name}.py"
    if not target.exists():
        return False
    target.unlink()
    return True
