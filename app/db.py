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

def create_conversation(
    pool: ConnectionPool, user_id: int, title: str, thread_id: str | None = None
) -> dict[str, Any]:
    conv_id = uuid.uuid4()
    thread_id = thread_id or f"thread-{conv_id.hex}"
    with pool.connection() as conn:
        row = conn.execute(
            "INSERT INTO conversations (id, user_id, title, thread_id) "
            "VALUES (%s, %s, %s, %s) RETURNING id, title, thread_id, created_at, updated_at",
            (conv_id, user_id, title[:100], thread_id),
        ).fetchone()
    return _conversation_dict(row)


def get_owned_conversation(
    pool: ConnectionPool, conversation_id: uuid.UUID | str, user_id: int
) -> dict[str, Any] | None:
    """按属主取会话；不存在或不属于该用户返回 None。"""
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT id, title, thread_id, created_at, updated_at, pinned "
            "FROM conversations WHERE id = %s AND user_id = %s",
            (str(conversation_id), user_id),
        ).fetchone()
    return _conversation_dict(row) if row else None


def list_conversations(
    pool: ConnectionPool, user_id: int, query: str | None = None
) -> list[dict[str, Any]]:
    """列出用户会话，置顶优先，其次按更新时间倒序。query 非空时按标题模糊搜索。"""
    sql = (
        "SELECT id, title, thread_id, created_at, updated_at, pinned "
        "FROM conversations WHERE user_id = %s"
    )
    params: list[Any] = [user_id]
    if query and query.strip():
        sql += " AND title ILIKE %s"
        params.append(f"%{query.strip()}%")
    sql += " ORDER BY pinned DESC, updated_at DESC"
    with pool.connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_conversation_dict(row) for row in rows]


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
    return {
        "id": str(row[0]),
        "title": row[1],
        "thread_id": row[2],
        "created_at": row[3].isoformat() if isinstance(row[3], datetime) else row[3],
        "updated_at": row[4].isoformat() if isinstance(row[4], datetime) else row[4],
        "pinned": bool(row[5]) if len(row) > 5 else False,
    }
