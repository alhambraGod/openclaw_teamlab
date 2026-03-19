"""
OpenClaw TeamLab — Chat Routes
Task submission via Redis queue, result polling, and conversation history.
"""
import json
import logging
from datetime import datetime
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from config.database import get_db, get_redis, rkey
from models import TaskLog, Conversation
from gateway.websocket import manager
from config.log_setup import QueueLogger

logger = logging.getLogger("teamlab.routes.chat")
_qlog = QueueLogger()
router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    user_id: Optional[str] = None
    source: str = "web"  # web | feishu | api
    callback_url: Optional[str] = None  # 任务完成后 POST 结果到此 URL，OpenClaw 可据此异步回传
    email: Optional[str] = None         # 用户指定邮箱，完成/超时时发送结果邮件


# ── Routes ──

@router.post("")
async def submit_chat(body: ChatRequest):
    """
    提交任务并立即返回。由 Worker 并行异步处理，提高并发。
    返回 task_id、队列位置和预估等待时间，方便前端展示进度。
    """
    task_id = uuid4().hex[:16]
    user_id = body.user_id or "anonymous"

    queue_position = 1
    estimated_wait_seconds = 10
    active_workers = 1

    # Push to Redis task queue
    try:
        r = await get_redis()
        payload_dict = {
            "task_id": task_id,
            "user_id": user_id,
            "source": body.source,
            "skill": "",
            "input_text": body.message,
            "created_at": datetime.utcnow().isoformat(),
        }
        if body.callback_url:
            payload_dict["callback_url"] = body.callback_url
        if body.email:
            payload_dict["email"] = body.email
        payload = json.dumps(payload_dict, ensure_ascii=False)
        await r.lpush(rkey("task_queue"), payload)

        # 获取队列位置（推入后的长度即为排队位置）
        queue_position = await r.llen(rkey("task_queue"))

        # 读取滚动平均耗时（Worker 每次完成后更新）
        avg_raw = await r.get(rkey("stats:avg_duration_ms"))
        avg_ms = int(avg_raw) if avg_raw else 25000  # 默认 25s

        # 统计活跃 Worker 数量
        workers_raw = await r.hgetall(rkey("workers"))
        active_workers = max(1, len(workers_raw))

        # ETA：(队列位置 / 并行Worker数) × 平均耗时
        from gateway.app import CONCURRENT_CONSUMERS
        parallelism = min(active_workers, CONCURRENT_CONSUMERS)
        estimated_wait_seconds = max(3, int((queue_position / parallelism) * avg_ms / 1000))

        _qlog.enqueue(task_id, "", user_id, body.source)
    except Exception as exc:
        logger.error("Failed to enqueue task: %s", exc)
        raise HTTPException(status_code=503, detail="Task queue unavailable")

    # Human-readable wait hint（在通知/返回之前计算）
    if estimated_wait_seconds < 8:
        wait_hint = "即将处理"
    elif estimated_wait_seconds < 60:
        wait_hint = f"约 {estimated_wait_seconds} 秒"
    else:
        wait_hint = f"约 {estimated_wait_seconds // 60} 分钟"

    # Record in claw_task_log (include callback_url for timeout/completion notification)
    try:
        async with get_db() as db:
            task = TaskLog(
                task_id=task_id,
                user_id=user_id,
                source=body.source,
                input_text=body.message,
                status="queued",
                callback_url=body.callback_url,
            )
            db.add(task)
    except Exception as exc:
        logger.warning("Failed to record task log: %s", exc)

    # Save user message to conversation history
    try:
        async with get_db() as db:
            db.add(Conversation(
                user_id=user_id,
                role="user",
                content=body.message,
            ))
    except Exception as exc:
        logger.warning("Failed to save conversation: %s", exc)

    # 注册 task→user 映射，定向推送排队通知（不广播给其他用户）
    try:
        from gateway.websocket import manager
        manager.register_task(task_id, user_id)
        await manager.send_to_user(user_id, {
            "type": "task_queued",
            "task_id": task_id,
            "queue_position": queue_position,
            "estimated_wait_seconds": estimated_wait_seconds,
            "wait_hint": wait_hint,
            "active_workers": active_workers,
        })
    except Exception:
        pass

    return {
        "task_id": task_id,
        "status": "queued",
        "queue_position": queue_position,
        "estimated_wait_seconds": estimated_wait_seconds,
        "wait_hint": wait_hint,
        "active_workers": active_workers,
        "message": f"已提交（第 {queue_position} 位，{wait_hint}）",
    }


@router.post("/result/{task_id}")
async def update_task_result(task_id: str, body: dict):
    """Internal callback: workers POST results here when a task completes."""
    try:
        async with get_db() as db:
            from sqlalchemy import update
            await db.execute(
                update(TaskLog)
                .where(TaskLog.task_id == task_id)
                .values(
                    status=body.get("status", "completed"),
                    skill_used=body.get("skill_used"),
                    result_summary=body.get("result_summary"),
                    result_data=body.get("result_data"),
                    worker_id=body.get("worker_id"),
                    duration_ms=body.get("duration_ms"),
                    error_message=body.get("error_message"),
                    completed_at=datetime.utcnow(),
                )
            )

        # Also save assistant reply to conversation
        if body.get("result_summary"):
            async with get_db() as db:
                # Look up the user_id from the task
                task = (await db.execute(
                    select(TaskLog).where(TaskLog.task_id == task_id)
                )).scalar_one_or_none()
                if task:
                    db.add(Conversation(
                        user_id=task.user_id,
                        role="assistant",
                        content=body["result_summary"],
                        skill_used=body.get("skill_used"),
                    ))

        # Push completion event to task owner only (targeted, not broadcast)
        await manager.send_to_task_owner(task_id, {
            "type": "task_update",
            "task_id": task_id,
            "status": body.get("status", "completed"),
            "result_summary": body.get("result_summary"),
            "skill_used": body.get("skill_used"),
            "duration_ms": body.get("duration_ms"),
            "error_message": body.get("error_message"),
        })
        manager.unregister_task(task_id)

    except Exception as exc:
        logger.error("Failed to update task result %s: %s", task_id, exc)

    return {"ok": True}


@router.get("/result/{task_id}")
async def get_task_result(task_id: str):
    """Poll for a task result. Uses raw SQL for reliability."""
    from sqlalchemy import text as sa_text
    async with get_db() as db:
        row = (await db.execute(
            sa_text(
                """
                SELECT task_id, status, skill_used, result_summary, result_data,
                       duration_ms, error_message, created_at, completed_at, timeout_at
                FROM claw_task_log
                WHERE task_id = :tid
                LIMIT 1
                """
            ),
            {"tid": task_id},
        )).mappings().first()

        if not row:
            raise HTTPException(status_code=404, detail="Task not found")

        return {
            "task_id": row["task_id"],
            "status": row["status"],
            "skill_used": row["skill_used"],
            "result_summary": row["result_summary"],
            "result_data": row["result_data"],
            "duration_ms": row["duration_ms"],
            "error_message": row["error_message"],
            "created_at": str(row["created_at"]) if row["created_at"] else None,
            "completed_at": str(row["completed_at"]) if row["completed_at"] else None,
            "timeout_at": str(row["timeout_at"]) if row["timeout_at"] else None,
        }


@router.get("/history/{user_id}")
async def chat_history(user_id: str):
    """Conversation history for a user."""
    async with get_db() as db:
        result = (await db.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.created_at.asc())
        )).scalars().all()

        return [
            {
                "id": c.id,
                "role": c.role,
                "content": c.content,
                "skill_used": c.skill_used,
                "metadata": c.metadata,
                "created_at": str(c.created_at) if c.created_at else None,
            }
            for c in result
        ]


@router.get("/logs")
async def chat_logs(source: str = None, limit: int = 100):
    """All task logs — optionally filtered by source (feishu/web). Includes input and result."""
    async with get_db() as db:
        q = select(TaskLog).order_by(TaskLog.created_at.desc()).limit(limit)
        if source:
            q = q.where(TaskLog.source == source)
        result = (await db.execute(q)).scalars().all()

        return [
            {
                "task_id": t.task_id,
                "user_id": t.user_id,
                "source": t.source,
                "skill_used": t.skill_used,
                "input_text": t.input_text,
                "result_summary": t.result_summary,
                "status": t.status,
                "duration_ms": t.duration_ms,
                "error_message": t.error_message,
                "created_at": str(t.created_at) if t.created_at else None,
                "completed_at": str(t.completed_at) if t.completed_at else None,
            }
            for t in result
        ]
