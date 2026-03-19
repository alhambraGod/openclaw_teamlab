"""
OpenClaw TeamLab — WebSocket Connection Manager

路由策略（三层定向推送，避免广播所有连接造成隐私泄露和性能浪费）：

  1. send_to(client_id, msg)     — 发送给单个 WebSocket 连接
  2. send_to_user(user_id, msg)  — 发送给某用户的所有连接（同一用户多端）
  3. broadcast(msg)              — 全量广播（仅用于系统公告等非敏感消息）

任务进度/结果推送应使用 send_to_user，不要使用 broadcast。
"""
import json
import logging
from collections import defaultdict
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect

logger = logging.getLogger("teamlab.ws")


class ConnectionManager:
    """Manages active WebSocket connections with user-aware routing."""

    def __init__(self):
        # client_id → WebSocket
        self.active: dict[str, WebSocket] = {}
        # user_id → set of client_ids（同一用户可能多设备连接）
        self._user_clients: dict[str, set[str]] = defaultdict(set)
        # task_id → user_id（任务创建时注册，完成时清除）
        self._task_user: dict[str, str] = {}

    async def connect(self, websocket: WebSocket, client_id: str, user_id: str | None = None):
        await websocket.accept()
        self.active[client_id] = websocket
        if user_id:
            self._user_clients[user_id].add(client_id)
        logger.info("WS connected: %s (user=%s, total=%d)", client_id, user_id, len(self.active))

    def disconnect(self, client_id: str):
        self.active.pop(client_id, None)
        # Remove from all user mappings
        for clients in self._user_clients.values():
            clients.discard(client_id)
        logger.info("WS disconnected: %s (total: %d)", client_id, len(self.active))

    def register_task(self, task_id: str, user_id: str):
        """Register which user owns a task for targeted delivery."""
        self._task_user[task_id] = user_id

    def unregister_task(self, task_id: str):
        self._task_user.pop(task_id, None)

    async def send_to(self, client_id: str, message: dict[str, Any]):
        """Send to a specific WebSocket connection."""
        ws = self.active.get(client_id)
        if ws is None:
            return
        try:
            await ws.send_text(json.dumps(message, ensure_ascii=False))
        except Exception:
            self.disconnect(client_id)

    async def send_to_user(self, user_id: str, message: dict[str, Any]):
        """
        Send to all WebSocket connections belonging to user_id.
        Falls back to broadcast if user_id is empty/unknown (anonymous users on web UI).
        """
        if not user_id or user_id == "anonymous":
            await self.broadcast(message)
            return
        client_ids = list(self._user_clients.get(user_id, []))
        if not client_ids:
            # User has no active WS connection — silently drop (they'll poll instead)
            return
        payload = json.dumps(message, ensure_ascii=False)
        stale: list[str] = []
        for cid in client_ids:
            ws = self.active.get(cid)
            if ws is None:
                stale.append(cid)
                continue
            try:
                await ws.send_text(payload)
            except Exception:
                stale.append(cid)
        for cid in stale:
            self.disconnect(cid)

    async def send_to_task_owner(self, task_id: str, message: dict[str, Any]):
        """Look up task owner and send to their connections."""
        user_id = self._task_user.get(task_id, "")
        await self.send_to_user(user_id, message)

    async def broadcast(self, message: dict[str, Any]):
        """
        Broadcast to ALL connected clients.
        Use sparingly — only for system-level announcements.
        For task progress, use send_to_user or send_to_task_owner.
        """
        payload = json.dumps(message, ensure_ascii=False)
        stale: list[str] = []
        for cid, ws in list(self.active.items()):
            try:
                await ws.send_text(payload)
            except Exception:
                stale.append(cid)
        for cid in stale:
            self.disconnect(cid)


manager = ConnectionManager()
