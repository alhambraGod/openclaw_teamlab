"""
OpenClaw TeamLab — Main API Router
Aggregates all sub-routers under /api prefix.

本系统作为数据后端运行（:10301），C-Si 专属 OpenClaw（:10300）
通过 bash+curl 调用 /api/agent/* HTTP 接口完成 PI 管理功能。
"""
import logging
from fastapi import APIRouter, Request
from gateway.websocket import manager
from config.log_setup import QueueLogger

from gateway.routes.dashboard import router as dashboard_router
from gateway.routes.students import router as students_router
from gateway.routes.meetings import router as meetings_router
from gateway.routes.directions import router as directions_router
from gateway.routes.collaborations import router as collaborations_router
from gateway.routes.chat import router as chat_router
from gateway.routes.system import router as system_router
from gateway.routes.coevo import router as coevo_router

logger = logging.getLogger("teamlab.gateway.api")

_qlog = QueueLogger()

api_router = APIRouter(prefix="/api")

api_router.include_router(dashboard_router)
api_router.include_router(students_router)
api_router.include_router(meetings_router)
api_router.include_router(directions_router)
api_router.include_router(collaborations_router)
api_router.include_router(chat_router)
api_router.include_router(system_router)
api_router.include_router(coevo_router)
# Agent API 在 gateway.app 中单独注册，确保加载顺序
# openclaw_bridge_router 已于 2026-03 移除（原用于宿主机 openclaw skill bridge，已废弃）


@api_router.post("/internal/task-complete")
async def internal_task_complete(request: Request):
    """Worker callback: task completed. Broadcast to WebSocket clients."""
    body = await request.json()
    task_id = body.get("task_id", "")
    worker_id = body.get("worker_id", "")
    status = body.get("status", "completed")
    duration_ms = body.get("duration_ms", 0)

    # 记录任务完成到队列调度日志
    _qlog.complete(task_id, worker_id, duration_ms, status)

    # 定向推送给任务所有者；如无 WS 连接则静默丢弃（客户端轮询可获取结果）
    await manager.send_to_task_owner(task_id, {
        "type": "task_update",
        "task_id": task_id,
        "status": status,
        "result_summary": body.get("result_summary", ""),
        "duration_ms": duration_ms,
    })
    manager.unregister_task(task_id)
    return {"ok": True}
