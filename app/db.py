"""业务数据库：用户 / 会话 / 消息。

复用同一个 PostgreSQL 实例（LangGraph checkpoints 表与之并存）。
使用 psycopg3 连接池，启动时幂等建表。
"""

import json
import uuid
from datetime import datetime
from typing import Any

from psycopg import errors as pg_errors
from psycopg_pool import ConnectionPool

from app.security.passwords import hash_password, verify_password

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    username      VARCHAR(32) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          VARCHAR(16) NOT NULL DEFAULT 'user',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 老库升级：补 role 列（幂等）
ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(16) NOT NULL DEFAULT 'user';

CREATE TABLE IF NOT EXISTS conversations (
    id         UUID PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      VARCHAR(100) NOT NULL,
    thread_id  VARCHAR(80) UNIQUE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    pinned     BOOLEAN NOT NULL DEFAULT FALSE
);
-- 老库升级：补 pinned 列（幂等）
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT FALSE;
-- 会话组织维度：多标签 + 单值分类 + 收藏（幂等迁移）
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS tags JSONB NOT NULL DEFAULT '[]';
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS category VARCHAR(32);
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS favorite BOOLEAN NOT NULL DEFAULT FALSE;
-- 协作围观：分享 token（为空表示未开启分享）
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS share_token VARCHAR(40);
CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_share_token
    ON conversations(share_token) WHERE share_token IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_conversations_user
    ON conversations(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id            BIGSERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role          VARCHAR(16) NOT NULL,
    content       TEXT NOT NULL,
    reasoning     TEXT,
    route         VARCHAR(16),
    sources       JSONB,
    tool_results  JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);

-- ==================== Team OS ====================

CREATE TABLE IF NOT EXISTS agent_roles (
    id            BIGSERIAL PRIMARY KEY,
    name          VARCHAR(32) UNIQUE NOT NULL,       -- pm / product / architect / developer / tester / reviewer
    display_name  VARCHAR(64) NOT NULL,
    description   TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    allowed_tools JSONB NOT NULL DEFAULT '[]',        -- 该角色可调用的工具名列表
    artifact_types JSONB NOT NULL DEFAULT '[]',       -- 产物类型：prd / architecture / code / test_report / review ...
    requires_human_confirm BOOLEAN NOT NULL DEFAULT FALSE,
    is_builtin    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS team_tasks (
    id            UUID PRIMARY KEY,
    team_id       UUID NOT NULL,                        -- 一次团队协作的标识
    conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role          VARCHAR(32) NOT NULL,                -- 对应 agent_roles.name
    title         VARCHAR(200) NOT NULL,
    description   TEXT,
    status        VARCHAR(16) NOT NULL DEFAULT 'pending', -- pending/running/blocked/done/failed/cancelled
    depends_on    JSONB NOT NULL DEFAULT '[]',         -- 前置任务 id 列表
    artifact_id   UUID,                                -- 关联产物
    error         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_team_tasks_team ON team_tasks(team_id);
CREATE INDEX IF NOT EXISTS idx_team_tasks_user ON team_tasks(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS team_artifacts (
    id            UUID PRIMARY KEY,
    task_id       UUID NOT NULL REFERENCES team_tasks(id) ON DELETE CASCADE,
    type          VARCHAR(32) NOT NULL,                -- prd / architecture / code / test_report / review
    title         VARCHAR(200) NOT NULL,
    content       TEXT NOT NULL,                       -- Markdown / JSON / diff
    version       INT NOT NULL DEFAULT 1,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_team_artifacts_task ON team_artifacts(task_id);

CREATE TABLE IF NOT EXISTS team_prs (
    id            UUID PRIMARY KEY,
    team_id       UUID NOT NULL,
    task_id       UUID NOT NULL REFERENCES team_tasks(id) ON DELETE CASCADE,
    title         VARCHAR(200) NOT NULL,
    diff_content  TEXT NOT NULL,               -- unified diff
    files_changed JSONB NOT NULL DEFAULT '[]',  -- 文件路径列表
    status        VARCHAR(16) NOT NULL DEFAULT 'pending', -- pending/approved/rejected/applied
    reviewer_notes TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_team_prs_team ON team_prs(team_id);

CREATE TABLE IF NOT EXISTS teams (
    id            UUID PRIMARY KEY,
    user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name          VARCHAR(100) NOT NULL,
    template      VARCHAR(32),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_teams_user ON teams(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS notifications (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    team_id    UUID,
    task_id    UUID,
    type       VARCHAR(32) NOT NULL,                  -- task_blocked / task_failed / team_done
    title      VARCHAR(200) NOT NULL,
    body       TEXT,
    is_read    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_notifications_user
    ON notifications(user_id, is_read, created_at DESC);

-- ==================== 批量导入 ====================

CREATE TABLE IF NOT EXISTS ingest_jobs (
    id            UUID PRIMARY KEY,
    user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status        VARCHAR(16) NOT NULL DEFAULT 'running',  -- running/done/failed
    total_files   INT NOT NULL,
    completed_files INT NOT NULL DEFAULT 0,
    failed_files  INT NOT NULL DEFAULT 0,
    results       JSONB NOT NULL DEFAULT '[]',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ingest_jobs_user ON ingest_jobs(user_id, created_at DESC);
"""


class UsernameTaken(Exception):
    """注册时用户名已存在。"""


class InvalidCredentials(Exception):
    """登录用户名或密码错误。"""


def create_pool(database_url: str, *, retries: int = 10, interval: float = 3.0) -> ConnectionPool:
    """建立连接池并执行幂等建表（PG 未就绪时自动重试）。"""
    last_error: Exception | None = None
    for _ in range(retries):
        try:
            pool = ConnectionPool(
                database_url,
                min_size=1,
                max_size=5,
                kwargs={"autocommit": True, "application_name": "edagent-web"},
                open=True,
                timeout=10,
            )
            with pool.connection() as conn:
                conn.execute(_SCHEMA)
            return pool
        except Exception as exc:  # 容器并行启动时 PG 可能尚未就绪
            last_error = exc
            import time

            time.sleep(interval)
    raise RuntimeError(f"无法连接业务数据库（已重试 {retries} 次）: {last_error}")


# ---------------- 用户 ----------------

def create_user(pool: ConnectionPool, username: str, password: str) -> dict[str, Any]:
    """创建用户；首个注册用户自动成为 admin（本地自托管场景的引导机制）。"""
    try:
        with pool.connection() as conn:
            exists = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
            role = "user" if exists else "admin"
            row = conn.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s) "
                "RETURNING id, username, role, created_at",
                (username, hash_password(password), role),
            ).fetchone()
    except pg_errors.UniqueViolation as exc:
        raise UsernameTaken("用户名已存在") from exc
    return {"id": row[0], "username": row[1], "role": row[2], "created_at": row[3]}


def authenticate(pool: ConnectionPool, username: str, password: str) -> dict[str, Any]:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, role, created_at FROM users WHERE username = %s",
            (username,),
        ).fetchone()
    if row is None or not verify_password(password, row[2]):
        raise InvalidCredentials("用户名或密码错误")
    return {"id": row[0], "username": row[1], "role": row[3], "created_at": row[4]}


def get_user(pool: ConnectionPool, user_id: int) -> dict[str, Any] | None:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT id, username, role, created_at FROM users WHERE id = %s", (user_id,)
        ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "username": row[1], "role": row[2], "created_at": row[3]}


# ---------------- 管理员 ----------------

def list_users(pool: ConnectionPool) -> list[dict[str, Any]]:
    """全部用户（含会话数），供管理端点使用。"""
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT u.id, u.username, u.role, u.created_at, COUNT(c.id) "
            "FROM users u LEFT JOIN conversations c ON c.user_id = u.id "
            "GROUP BY u.id ORDER BY u.id"
        ).fetchall()
    return [
        {
            "id": row[0],
            "username": row[1],
            "role": row[2],
            "created_at": row[3].isoformat() if isinstance(row[3], datetime) else row[3],
            "conversations": int(row[4]),
        }
        for row in rows
    ]


def set_user_role(pool: ConnectionPool, user_id: int, role: str) -> bool:
    with pool.connection() as conn:
        result = conn.execute(
            "UPDATE users SET role = %s WHERE id = %s", (role, user_id)
        )
        return result.rowcount > 0


def count_admins(pool: ConnectionPool) -> int:
    with pool.connection() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0])


def delete_user(pool: ConnectionPool, user_id: int) -> bool:
    """删除用户及其会话/消息/检查点；用户不存在返回 False。

    conversations/messages 由外键级联删除；LangGraph checkpoints 表
    不在业务外键体系内，按 thread_id 手工清理。
    """
    with pool.connection() as conn:
        thread_rows = conn.execute(
            "SELECT thread_id FROM conversations WHERE user_id = %s", (user_id,)
        ).fetchall()
        result = conn.execute("DELETE FROM users WHERE id = %s", (user_id,))
        if result.rowcount == 0:
            return False
        for (thread_id,) in thread_rows:
            conn.execute("DELETE FROM checkpoints WHERE thread_id = %s", (thread_id,))
            conn.execute("DELETE FROM checkpoint_blobs WHERE thread_id = %s", (thread_id,))
            conn.execute("DELETE FROM checkpoint_writes WHERE thread_id = %s", (thread_id,))
    return True


# ---------------- 会话 ----------------

# 会话行统一列序（_conversation_dict 依赖该顺序，勿随意调整）
_CONV_COLS = (
    "id, title, thread_id, created_at, updated_at, pinned, "
    "tags, category, favorite, share_token"
)

# update_conversation_meta 允许写入的列（白名单，防止 SQL 拼接注入列名）
_CONV_META_COLUMNS = ("tags", "category", "favorite")


def create_conversation(
    pool: ConnectionPool, user_id: int, title: str, thread_id: str | None = None
) -> dict[str, Any]:
    conv_id = uuid.uuid4()
    thread_id = thread_id or f"thread-{conv_id.hex}"
    with pool.connection() as conn:
        row = conn.execute(
            "INSERT INTO conversations (id, user_id, title, thread_id) "
            f"VALUES (%s, %s, %s, %s) RETURNING {_CONV_COLS}",
            (conv_id, user_id, title[:100], thread_id),
        ).fetchone()
    return _conversation_dict(row)


def get_owned_conversation(
    pool: ConnectionPool, conversation_id: uuid.UUID | str, user_id: int
) -> dict[str, Any] | None:
    """按属主取会话；不存在或不属于该用户返回 None。"""
    with pool.connection() as conn:
        row = conn.execute(
            f"SELECT {_CONV_COLS} FROM conversations WHERE id = %s AND user_id = %s",
            (str(conversation_id), user_id),
        ).fetchone()
    return _conversation_dict(row) if row else None


def list_conversations(
    pool: ConnectionPool,
    user_id: int,
    query: str | None = None,
    *,
    tag: str | None = None,
    category: str | None = None,
    favorite: bool = False,
) -> list[dict[str, Any]]:
    """列出用户会话，置顶优先，其次按更新时间倒序。

    支持多维筛选：标题模糊搜索（query）、标签（tag）、分类（category）、仅收藏。
    """
    sql = f"SELECT {_CONV_COLS} FROM conversations WHERE user_id = %s"
    params: list[Any] = [user_id]
    if query and query.strip():
        sql += " AND title ILIKE %s"
        params.append(f"%{query.strip()}%")
    if tag and tag.strip():
        # jsonb 包含判断：tags @> '["标签"]'
        sql += " AND tags @> %s::jsonb"
        params.append(json.dumps([tag.strip()], ensure_ascii=False))
    if category and category.strip():
        sql += " AND category = %s"
        params.append(category.strip())
    if favorite:
        sql += " AND favorite = TRUE"
    sql += " ORDER BY pinned DESC, updated_at DESC"
    with pool.connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_conversation_dict(row) for row in rows]


def list_conversation_filters(pool: ConnectionPool, user_id: int) -> dict[str, Any]:
    """聚合用户全部会话的分类与标签（含使用次数），供侧栏筛选下拉使用。"""
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT tags, category FROM conversations WHERE user_id = %s", (user_id,)
        ).fetchall()
    tag_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    for tags, category in rows:
        for name in tags or []:
            tag_counts[name] = tag_counts.get(name, 0) + 1
        if category:
            category_counts[category] = category_counts.get(category, 0) + 1
    return {
        "tags": [
            {"name": name, "count": count}
            for name, count in sorted(tag_counts.items(), key=lambda i: (-i[1], i[0]))
        ],
        "categories": [
            {"name": name, "count": count}
            for name, count in sorted(
                category_counts.items(), key=lambda i: (-i[1], i[0])
            )
        ],
    }


def update_conversation_meta(
    pool: ConnectionPool,
    conversation_id: str,
    user_id: int,
    changes: dict[str, Any],
) -> dict[str, Any] | None:
    """局部更新会话的 tags / category / favorite；会话不存在或非本人返回 None。

    changes 只接受白名单列（见 _CONV_META_COLUMNS），未包含的列保持不变。
    category 传 None 表示清空分类。
    """
    updates = {k: v for k, v in changes.items() if k in _CONV_META_COLUMNS}
    if not updates:
        return get_owned_conversation(pool, conversation_id, user_id)

    sets: list[str] = []
    params: list[Any] = []
    for column, value in updates.items():
        sets.append(f"{column} = %s")
        params.append(
            json.dumps(value, ensure_ascii=False) if column == "tags" else value
        )
    params += [str(conversation_id), user_id]

    with pool.connection() as conn:
        result = conn.execute(
            f"UPDATE conversations SET {', '.join(sets)} WHERE id = %s AND user_id = %s",
            params,
        )
        if result.rowcount == 0:
            return None
        row = conn.execute(
            f"SELECT {_CONV_COLS} FROM conversations WHERE id = %s",
            (str(conversation_id),),
        ).fetchone()
    return _conversation_dict(row) if row else None


def enable_share(pool: ConnectionPool, conversation_id: str, user_id: int) -> str | None:
    """开启协作围观并返回分享 token；已开启则沿用原 token。会话不存在返回 None。"""
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT share_token FROM conversations WHERE id = %s AND user_id = %s",
            (str(conversation_id), user_id),
        ).fetchone()
        if row is None:
            return None
        if row[0]:
            return row[0]
        token = uuid.uuid4().hex[:24]
        conn.execute(
            "UPDATE conversations SET share_token = %s WHERE id = %s",
            (token, str(conversation_id)),
        )
        return token


def get_share_token(pool: ConnectionPool, conversation_id: str, user_id: int) -> str | None:
    """读取会话的分享 token（未开启为 None，会话不存在或非本人也为 None）。"""
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT share_token FROM conversations WHERE id = %s AND user_id = %s",
            (str(conversation_id), user_id),
        ).fetchone()
    return row[0] if row else None


def disable_share(pool: ConnectionPool, conversation_id: str, user_id: int) -> bool:
    """关闭协作围观（清空 token）；会话不存在或非本人返回 False。"""
    with pool.connection() as conn:
        result = conn.execute(
            "UPDATE conversations SET share_token = NULL WHERE id = %s AND user_id = %s",
            (str(conversation_id), user_id),
        )
        return result.rowcount > 0


def get_shared_conversation(pool: ConnectionPool, share_token: str) -> dict[str, Any] | None:
    """按分享 token 取会话（含发起人用户名），供协作围观者只读访问。"""
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT c.id, c.title, c.thread_id, c.created_at, c.updated_at, c.pinned, "
            "c.tags, c.category, c.favorite, c.share_token, u.username "
            "FROM conversations c JOIN users u ON u.id = c.user_id "
            "WHERE c.share_token = %s",
            (share_token,),
        ).fetchone()
    if row is None:
        return None
    conv = _conversation_dict(row[:10])
    conv["owner_username"] = row[10]
    conv["shared"] = True
    return conv


def touch_conversation(pool: ConnectionPool, conversation_id: str) -> None:
    with pool.connection() as conn:
        conn.execute(
            "UPDATE conversations SET updated_at = now() WHERE id = %s",
            (conversation_id,),
        )


def toggle_pin(pool: ConnectionPool, conversation_id: str, user_id: int) -> bool | None:
    """切换会话置顶状态；返回新状态（True=已置顶 / False=取消），不存在返回 None。"""
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT pinned FROM conversations WHERE id = %s AND user_id = %s",
            (conversation_id, user_id),
        ).fetchone()
        if row is None:
            return None
        new_val = not row[0]
        conn.execute(
            "UPDATE conversations SET pinned = %s WHERE id = %s AND user_id = %s",
            (new_val, conversation_id, user_id),
        )
        return new_val


def delete_conversation(pool: ConnectionPool, conversation_id: str, user_id: int) -> bool:
    with pool.connection() as conn:
        result = conn.execute(
            "DELETE FROM conversations WHERE id = %s AND user_id = %s",
            (conversation_id, user_id),
        )
        return result.rowcount > 0


# ---------------- 消息 ----------------

def add_message(
    pool: ConnectionPool,
    conversation_id: str,
    role: str,
    content: str,
    *,
    reasoning: str | None = None,
    route: str | None = None,
    sources: list[dict[str, Any]] | None = None,
    tool_results: list[dict[str, Any]] | None = None,
) -> None:
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO messages "
            "(conversation_id, role, content, reasoning, route, sources, tool_results) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                conversation_id,
                role,
                content,
                reasoning,
                route,
                json.dumps(sources or [], ensure_ascii=False),
                json.dumps(tool_results or [], ensure_ascii=False),
            ),
        )
    touch_conversation(pool, conversation_id)


def list_messages(pool: ConnectionPool, conversation_id: str) -> list[dict[str, Any]]:
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT role, content, reasoning, route, sources, tool_results, created_at "
            "FROM messages WHERE conversation_id = %s ORDER BY id",
            (conversation_id,),
        ).fetchall()

    messages = []
    for row in rows:
        messages.append(
            {
                "role": row[0],
                "content": row[1],
                "reasoning": row[2],
                "route": row[3],
                "sources": row[4] or [],
                "tool_results": row[5] or [],
                "created_at": row[6].isoformat() if isinstance(row[6], datetime) else row[6],
            }
        )
    return messages


def _conversation_dict(row: tuple) -> dict[str, Any]:
    """把 _CONV_COLS 顺序的行转为字典（字段顺序：id/标题/线程/时间/置顶/标签/分类/收藏/分享 token）。"""
    return {
        "id": str(row[0]),
        "title": row[1],
        "thread_id": row[2],
        "created_at": row[3].isoformat() if isinstance(row[3], datetime) else row[3],
        "updated_at": row[4].isoformat() if isinstance(row[4], datetime) else row[4],
        "pinned": bool(row[5]),
        "tags": list(row[6] or []),
        "category": row[7],
        "favorite": bool(row[8]),
        "share_token": row[9],
    }


# ==================== Team OS: 角色 / 任务 / 产物 ====================

def upsert_role(
    pool: ConnectionPool,
    name: str,
    display_name: str,
    description: str,
    system_prompt: str,
    allowed_tools: list[str],
    artifact_types: list[str],
    requires_human_confirm: bool = False,
    is_builtin: bool = True,
) -> None:
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO agent_roles (name, display_name, description, system_prompt, "
            "allowed_tools, artifact_types, requires_human_confirm, is_builtin) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (name) DO UPDATE SET "
            "display_name = EXCLUDED.display_name, "
            "description = EXCLUDED.description, "
            "system_prompt = EXCLUDED.system_prompt, "
            "allowed_tools = EXCLUDED.allowed_tools, "
            "artifact_types = EXCLUDED.artifact_types, "
            "requires_human_confirm = EXCLUDED.requires_human_confirm",
            (
                name, display_name, description, system_prompt,
                json.dumps(allowed_tools, ensure_ascii=False),
                json.dumps(artifact_types, ensure_ascii=False),
                requires_human_confirm, is_builtin,
            ),
        )


def list_roles(pool: ConnectionPool) -> list[dict[str, Any]]:
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT id, name, display_name, description, system_prompt, "
            "allowed_tools, artifact_types, requires_human_confirm, is_builtin, created_at "
            "FROM agent_roles ORDER BY id"
        ).fetchall()
    return [
        {
            "id": row[0], "name": row[1], "display_name": row[2], "description": row[3],
            "system_prompt": row[4], "allowed_tools": row[5] or [],
            "artifact_types": row[6] or [], "requires_human_confirm": row[7],
            "is_builtin": row[8], "created_at": str(row[9]),
        }
        for row in rows
    ]


def get_role(pool: ConnectionPool, name: str) -> dict[str, Any] | None:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT id, name, display_name, description, system_prompt, "
            "allowed_tools, artifact_types, requires_human_confirm, is_builtin "
            "FROM agent_roles WHERE name = %s",
            (name,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0], "name": row[1], "display_name": row[2], "description": row[3],
        "system_prompt": row[4], "allowed_tools": row[5] or [],
        "artifact_types": row[6] or [], "requires_human_confirm": row[7], "is_builtin": row[8],
    }


def create_team_task(
    pool: ConnectionPool,
    *,
    task_id: str,
    team_id: str,
    conversation_id: str | None,
    user_id: int,
    role: str,
    title: str,
    description: str = "",
    depends_on: list[str] | None = None,
) -> None:
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO team_tasks "
            "(id, team_id, conversation_id, user_id, role, title, description, depends_on) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                task_id, team_id, conversation_id, user_id, role, title,
                description, json.dumps(depends_on or [], ensure_ascii=False),
            ),
        )


def list_team_tasks(pool: ConnectionPool, team_id: str) -> list[dict[str, Any]]:
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT id, team_id, conversation_id, user_id, role, title, description, "
            "status, depends_on, artifact_id, error, created_at, updated_at "
            "FROM team_tasks WHERE team_id = %s ORDER BY created_at",
            (team_id,),
        ).fetchall()
    return [
        {
            "id": str(row[0]), "team_id": str(row[1]),
            "conversation_id": str(row[2]) if row[2] else None,
            "user_id": row[3], "role": row[4], "title": row[5], "description": row[6],
            "status": row[7], "depends_on": row[8] or [],
            "artifact_id": str(row[9]) if row[9] else None, "error": row[10],
            "created_at": str(row[11]), "updated_at": str(row[12]),
        }
        for row in rows
    ]


def update_task_status(
    pool: ConnectionPool,
    task_id: str,
    status: str,
    *,
    error: str | None = None,
    artifact_id: str | None = None,
) -> None:
    with pool.connection() as conn:
        conn.execute(
            "UPDATE team_tasks SET status = %s, error = %s, artifact_id = %s, updated_at = now() "
            "WHERE id = %s",
            (status, error, artifact_id, task_id),
        )


def create_artifact(
    pool: ConnectionPool,
    *,
    artifact_id: str,
    task_id: str,
    type: str,
    title: str,
    content: str,
    version: int = 1,
) -> None:
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO team_artifacts (id, task_id, type, title, content, version) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (artifact_id, task_id, type, title, content, version),
        )


def get_artifact(pool: ConnectionPool, artifact_id: str) -> dict[str, Any] | None:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT id, task_id, type, title, content, version, created_at "
            "FROM team_artifacts WHERE id = %s",
            (artifact_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": str(row[0]), "task_id": str(row[1]), "type": row[2],
        "title": row[3], "content": row[4], "version": row[5],
        "created_at": str(row[6]),
    }


def list_artifacts_by_team(pool: ConnectionPool, team_id: str) -> list[dict[str, Any]]:
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT a.id, a.task_id, a.type, a.title, a.content, a.version, a.created_at "
            "FROM team_artifacts a "
            "JOIN team_tasks t ON a.task_id = t.id "
            "WHERE t.team_id = %s ORDER BY a.created_at",
            (team_id,),
        ).fetchall()
    return [
        {
            "id": str(row[0]), "task_id": str(row[1]), "type": row[2],
            "title": row[3], "content": row[4], "version": row[5],
            "created_at": str(row[6]),
        }
        for row in rows
    ]


# ==================== Team OS: PR / 补丁 ====================

def create_pr(
    pool: ConnectionPool,
    *,
    pr_id: str,
    team_id: str,
    task_id: str,
    title: str,
    diff_content: str,
    files_changed: list[str],
) -> None:
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO team_prs (id, team_id, task_id, title, diff_content, files_changed) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (pr_id, team_id, task_id, title, diff_content,
             json.dumps(files_changed, ensure_ascii=False)),
        )


def list_prs(pool: ConnectionPool, team_id: str) -> list[dict[str, Any]]:
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT id, team_id, task_id, title, diff_content, files_changed, "
            "status, reviewer_notes, created_at, reviewed_at "
            "FROM team_prs WHERE team_id = %s ORDER BY created_at",
            (team_id,),
        ).fetchall()
    return [
        {
            "id": str(row[0]), "team_id": str(row[1]), "task_id": str(row[2]),
            "title": row[3], "diff_content": row[4], "files_changed": row[5] or [],
            "status": row[6], "reviewer_notes": row[7],
            "created_at": str(row[8]), "reviewed_at": str(row[9]) if row[9] else None,
        }
        for row in rows
    ]


def get_pr(pool: ConnectionPool, pr_id: str) -> dict[str, Any] | None:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT id, team_id, task_id, title, diff_content, files_changed, "
            "status, reviewer_notes, created_at, reviewed_at "
            "FROM team_prs WHERE id = %s",
            (pr_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": str(row[0]), "team_id": str(row[1]), "task_id": str(row[2]),
        "title": row[3], "diff_content": row[4], "files_changed": row[5] or [],
        "status": row[6], "reviewer_notes": row[7],
        "created_at": str(row[8]), "reviewed_at": str(row[9]) if row[9] else None,
    }


def update_pr_status(
    pool: ConnectionPool,
    pr_id: str,
    status: str,
    *,
    reviewer_notes: str | None = None,
) -> None:
    with pool.connection() as conn:
        conn.execute(
            "UPDATE team_prs SET status = %s, reviewer_notes = %s, reviewed_at = now() "
            "WHERE id = %s",
            (status, reviewer_notes, pr_id),
        )


# ==================== Team OS: 团队 / 通知 ====================

def create_team(
    pool: ConnectionPool,
    *,
    team_id: str,
    user_id: int,
    name: str,
    template: str | None = None,
) -> None:
    """创建团队记录（一个 team_id 对应一次团队协作）。"""
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO teams (id, user_id, name, template) VALUES (%s, %s, %s, %s)",
            (team_id, user_id, name[:100], template),
        )


def list_teams(pool: ConnectionPool, user_id: int) -> list[dict[str, Any]]:
    """列出用户的团队，附带任务数 / 产物数 / 状态汇总。"""
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT t.id, t.name, t.template, t.created_at, t.updated_at, "
            "COUNT(tt.id) AS task_count, "
            "COUNT(tt.id) FILTER (WHERE tt.status = 'done') AS done_count, "
            "COUNT(tt.id) FILTER (WHERE tt.status = 'running') AS running_count, "
            "COUNT(tt.id) FILTER (WHERE tt.status = 'blocked') AS blocked_count, "
            "COUNT(tt.id) FILTER (WHERE tt.status = 'failed') AS failed_count, "
            "COUNT(tt.id) FILTER (WHERE tt.status = 'pending') AS pending_count, "
            "(SELECT COUNT(*) FROM team_artifacts a "
            " JOIN team_tasks at2 ON a.task_id = at2.id WHERE at2.team_id = t.id) "
            "AS artifact_count "
            "FROM teams t LEFT JOIN team_tasks tt ON tt.team_id = t.id "
            "WHERE t.user_id = %s GROUP BY t.id ORDER BY t.updated_at DESC",
            (user_id,),
        ).fetchall()
    teams: list[dict[str, Any]] = []
    for row in rows:
        summary = {
            "done": int(row[6]),
            "running": int(row[7]),
            "blocked": int(row[8]),
            "failed": int(row[9]),
            "pending": int(row[10]),
        }
        teams.append(
            {
                "id": str(row[0]),
                "name": row[1],
                "template": row[2],
                "created_at": row[3].isoformat() if isinstance(row[3], datetime) else row[3],
                "updated_at": row[4].isoformat() if isinstance(row[4], datetime) else row[4],
                "task_count": int(row[5]),
                "artifact_count": int(row[11]),
                "status_summary": {k: v for k, v in summary.items() if v},
            }
        )
    return teams


def get_team_record(pool: ConnectionPool, team_id: str) -> dict[str, Any] | None:
    """按 id 取团队记录；不存在返回 None。"""
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT id, user_id, name, template, created_at, updated_at "
            "FROM teams WHERE id = %s",
            (team_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": str(row[0]),
        "user_id": row[1],
        "name": row[2],
        "template": row[3],
        "created_at": row[4].isoformat() if isinstance(row[4], datetime) else row[4],
        "updated_at": row[5].isoformat() if isinstance(row[5], datetime) else row[5],
    }


def rename_team(pool: ConnectionPool, team_id: str, name: str) -> bool:
    """重命名团队；团队不存在返回 False。"""
    with pool.connection() as conn:
        result = conn.execute(
            "UPDATE teams SET name = %s, updated_at = now() WHERE id = %s",
            (name[:100], team_id),
        )
        return result.rowcount > 0


def delete_team(pool: ConnectionPool, team_id: str) -> bool:
    """删除团队及其 PR / 产物 / 任务；团队不存在返回 False。"""
    with pool.connection() as conn:
        conn.execute("DELETE FROM team_prs WHERE team_id = %s", (team_id,))
        conn.execute(
            "DELETE FROM team_artifacts WHERE task_id IN "
            "(SELECT id FROM team_tasks WHERE team_id = %s)",
            (team_id,),
        )
        conn.execute("DELETE FROM team_tasks WHERE team_id = %s", (team_id,))
        result = conn.execute("DELETE FROM teams WHERE id = %s", (team_id,))
        return result.rowcount > 0


def create_notification(
    pool: ConnectionPool,
    *,
    user_id: int,
    type: str,
    title: str,
    body: str = "",
    team_id: str | None = None,
    task_id: str | None = None,
) -> None:
    """写入一条用户通知。"""
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO notifications (user_id, team_id, task_id, type, title, body) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (user_id, team_id, task_id, type, title[:200], body),
        )


def list_notifications(
    pool: ConnectionPool,
    user_id: int,
    *,
    unread_only: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """列出用户通知，未读优先，其次按时间倒序。"""
    sql = (
        "SELECT id, team_id, task_id, type, title, body, is_read, created_at "
        "FROM notifications WHERE user_id = %s"
    )
    params: list[Any] = [user_id]
    if unread_only:
        sql += " AND is_read = FALSE"
    sql += " ORDER BY is_read ASC, created_at DESC LIMIT %s"
    params.append(limit)
    with pool.connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {
            "id": row[0],
            "team_id": str(row[1]) if row[1] else None,
            "task_id": str(row[2]) if row[2] else None,
            "type": row[3],
            "title": row[4],
            "body": row[5] or "",
            "is_read": bool(row[6]),
            "created_at": row[7].isoformat() if isinstance(row[7], datetime) else row[7],
        }
        for row in rows
    ]


def mark_notification_read(pool: ConnectionPool, notification_id: int, user_id: int) -> bool:
    """标记单条通知为已读；不存在或非本人返回 False。"""
    with pool.connection() as conn:
        result = conn.execute(
            "UPDATE notifications SET is_read = TRUE WHERE id = %s AND user_id = %s",
            (notification_id, user_id),
        )
        return result.rowcount > 0


def mark_all_notifications_read(pool: ConnectionPool, user_id: int) -> int:
    """把用户所有未读通知标记为已读，返回影响行数。"""
    with pool.connection() as conn:
        result = conn.execute(
            "UPDATE notifications SET is_read = TRUE WHERE user_id = %s AND is_read = FALSE",
            (user_id,),
        )
        return result.rowcount


def count_unread_notifications(pool: ConnectionPool, user_id: int) -> int:
    with pool.connection() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM notifications WHERE user_id = %s AND is_read = FALSE",
                (user_id,),
            ).fetchone()[0]
        )


# ==================== 批量导入 ====================

def create_ingest_job(
    pool: ConnectionPool, *, job_id: str, user_id: int, total_files: int
) -> None:
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO ingest_jobs (id, user_id, total_files) VALUES (%s, %s, %s)",
            (job_id, user_id, total_files),
        )


def update_ingest_job(
    pool: ConnectionPool,
    job_id: str,
    *,
    status: str | None = None,
    completed_files: int | None = None,
    failed_files: int | None = None,
    results: list[dict] | None = None,
) -> None:
    with pool.connection() as conn:
        if status is not None:
            conn.execute(
                "UPDATE ingest_jobs SET status = %s, updated_at = now() WHERE id = %s",
                (status, job_id),
            )
        if completed_files is not None:
            conn.execute(
                "UPDATE ingest_jobs SET completed_files = %s, updated_at = now() WHERE id = %s",
                (completed_files, job_id),
            )
        if failed_files is not None:
            conn.execute(
                "UPDATE ingest_jobs SET failed_files = %s, updated_at = now() WHERE id = %s",
                (failed_files, job_id),
            )
        if results is not None:
            conn.execute(
                "UPDATE ingest_jobs SET results = %s, updated_at = now() WHERE id = %s",
                (json.dumps(results, ensure_ascii=False), job_id),
            )


def get_ingest_job(
    pool: ConnectionPool, job_id: str, user_id: int
) -> dict[str, Any] | None:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT id, user_id, status, total_files, completed_files, failed_files, "
            "results, created_at, updated_at "
            "FROM ingest_jobs WHERE id = %s AND user_id = %s",
            (job_id, user_id),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": str(row[0]),
        "user_id": row[1],
        "status": row[2],
        "total_files": row[3],
        "completed_files": row[4],
        "failed_files": row[5],
        "results": row[6] or [],
        "created_at": row[7].isoformat() if isinstance(row[7], datetime) else row[7],
        "updated_at": row[8].isoformat() if isinstance(row[8], datetime) else row[8],
    }
