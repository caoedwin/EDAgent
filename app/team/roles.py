"""角色注册表：从 YAML 文件加载角色定义并同步到数据库。"""

from pathlib import Path
from typing import Any

import yaml

from app import db

ROLES_DIR = Path(__file__).parent / "roles"


def load_role_definitions() -> list[dict[str, Any]]:
    """从 roles/*.yaml 加载所有角色定义。"""
    roles: list[dict[str, Any]] = []
    for path in sorted(ROLES_DIR.glob("*.yaml")):
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        roles.append(data)
    return roles


def sync_roles_to_db(pool) -> list[dict[str, Any]]:
    """把 YAML 中的角色定义 upsert 到 agent_roles 表，返回当前所有角色。"""
    for role in load_role_definitions():
        db.upsert_role(
            pool,
            name=role["name"],
            display_name=role["display_name"],
            description=role["description"],
            system_prompt=role["system_prompt"],
            allowed_tools=role.get("allowed_tools", []),
            artifact_types=role.get("artifact_types", []),
            requires_human_confirm=role.get("requires_human_confirm", False),
            is_builtin=True,
        )
    return db.list_roles(pool)
