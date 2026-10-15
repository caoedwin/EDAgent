"""协作围观实时广播：进程内 WebSocket 连接管理。

uvicorn 以单 worker 运行（见 Dockerfile CMD），因此进程内字典即可完成房间广播，
无需 Redis pub/sub 等跨进程通道；若将来改为多 worker，需替换为外部分发。
"""

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("edagent.collab")


class CollaborationHub:
    """按房间（会话分享 token）维护 WebSocket 连接并广播事件。"""

    def __init__(self) -> None:
        self._rooms: dict[str, dict[WebSocket, dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def join(self, room: str, websocket: WebSocket, user: dict[str, Any]) -> None:
        async with self._lock:
            self._rooms.setdefault(room, {})[websocket] = user

    async def leave(self, room: str, websocket: WebSocket) -> None:
        async with self._lock:
            members = self._rooms.get(room)
            if not members:
                return
            members.pop(websocket, None)
            if not members:
                self._rooms.pop(room, None)

    def members(self, room: str) -> list[dict[str, Any]]:
        """在线成员（同一用户多标签页去重）。"""
        unique: dict[int, dict[str, Any]] = {}
        for user in self._rooms.get(room, {}).values():
            unique[user["id"]] = {"id": user["id"], "username": user["username"]}
        return list(unique.values())

    def viewer_count(self, room: str) -> int:
        return len(self.members(room))

    async def close_room(
        self, room: str, payload: dict[str, Any] | None = None, code: int = 4404
    ) -> None:
        """关闭房间内所有连接（如分享被撤销时强制解散围观）。"""
        async with self._lock:
            members = list(self._rooms.pop(room, {}))
        for websocket in members:
            try:
                if payload is not None:
                    await websocket.send_json(payload)
                await websocket.close(code=code)
            except Exception:  # 连接已断开：忽略
                logger.debug("关闭连接失败 room=%s", room, exc_info=True)

    async def broadcast(
        self,
        room: str,
        payload: dict[str, Any],
        *,
        exclude: WebSocket | None = None,
    ) -> None:
        """向房间内所有连接推送事件；发送失败的连接自动剔除。"""
        for websocket in list(self._rooms.get(room, {})):
            if websocket is exclude:
                continue
            try:
                await websocket.send_json(payload)
            except Exception:  # 连接已断开：静默清理，不影响其他订阅者
                logger.debug("广播失败，移除连接 room=%s", room, exc_info=True)
                await self.leave(room, websocket)


hub = CollaborationHub()
