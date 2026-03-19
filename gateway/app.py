"""
OpenClaw TeamLab — FastAPI Gateway Application
Single entry point: serves the web UI, REST API, and WebSocket endpoint.

并发设计：
  - CONCURRENT_CONSUMERS 个并行队列消费者，Redis BRPOP 保证原子性不重复消费
  - Redis Pub/Sub 监听 task:progress 频道，实时转发进度事件到 WebSocket 客户端
"""
import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config.database import init_db, close_db, get_redis, rkey
from config.coevo_db import init_coevo_db, close_coevo_db
from config.settings import settings
from config.log_setup import QueueLogger
from gateway.websocket import manager

logger = logging.getLogger("teamlab.gateway")
_qlog = QueueLogger()

# 并行队列消费者数量：Redis BRPOP 原子性保证不重复消费
CONCURRENT_CONSUMERS = 5

# Auto-scale 检查周期（秒）
AUTO_SCALE_INTERVAL = 15

# ── Background tasks ──
_health_task: asyncio.Task | None = None
_pubsub_task: asyncio.Task | None = None
_scale_task: asyncio.Task | None = None
_watchdog_task: asyncio.Task | None = None
_queue_tasks: list[asyncio.Task] = []


async def _worker_health_monitor():
    """Periodically update gateway heartbeat in Redis."""
    try:
        while True:
            try:
                r = await get_redis()
                await r.set(rkey("gateway:heartbeat"), "alive", ex=30)
            except Exception as exc:
                logger.warning("Health monitor error: %s", exc)
            await asyncio.sleep(10)
    except asyncio.CancelledError:
        pass


async def _auto_scale_loop():
    """
    Periodic auto-scale loop running in the Gateway process.
    Reads Redis worker status and spawns/removes workers based on load.
    """
    try:
        while True:
            await asyncio.sleep(AUTO_SCALE_INTERVAL)
            try:
                from workers.pool import auto_scale
                await auto_scale()
            except Exception as exc:
                logger.debug("Auto-scale check failed: %s", exc)
    except asyncio.CancelledError:
        pass


# 任务超时阈值（秒）；watchdog 扫描时以此判定"卡住"任务
# 比 worker 侧的 TASK_TIMEOUT_SECONDS(180) 多留 60s 容错
WATCHDOG_TIMEOUT_SECONDS = int(os.environ.get("TASK_TIMEOUT_SECONDS", "180")) + 60


async def _task_watchdog():
    """
    后台看门狗：每 60 秒扫描一次 MySQL，
    将卡住超时（status IN running/queued 且 created_at 超过阈值）的任务强制标记为 timeout，
    并通过 WebSocket 通知任务所有者，POST callback_url（飞书/CLI 通知）。

    弥补 Worker 崩溃/重启导致任务状态未更新的场景。
    """
    logger.info("Task watchdog started (threshold=%ds)", WATCHDOG_TIMEOUT_SECONDS)
    try:
        while True:
            await asyncio.sleep(60)
            try:
                await _run_watchdog_cycle()
            except Exception as exc:
                logger.error("Watchdog cycle error: %s", exc)
    except asyncio.CancelledError:
        logger.info("Task watchdog stopped")


async def _run_watchdog_cycle():
    """Single watchdog scan cycle."""
    from sqlalchemy import text as sa_text
    from config.database import get_db

    timeout_msg_tpl = (
        "⏳ 您好！这个问题的分析超过了 {min} 分钟，系统已自动回收资源。\n\n"
        "您的问题：「{input}」\n\n"
        "💡 建议：\n"
        "• 稍后重试（已有缓存时会快很多）\n"
        "• 将问题拆解，如先查成员信息再查合作推荐\n"
        "• 通过 POST /api/chat 提交为后台异步任务，完成后自动通知"
    )

    async with get_db() as db:
        # 找出卡住任务（running/queued 且超过阈值）
        rows = (await db.execute(
            sa_text(
                """
                SELECT task_id, user_id, source, input_text, callback_url,
                       TIMESTAMPDIFF(SECOND, created_at, NOW()) AS age_sec
                FROM claw_task_log
                WHERE status IN ('running', 'queued')
                  AND created_at < DATE_SUB(NOW(), INTERVAL :threshold SECOND)
                LIMIT 50
                """
            ),
            {"threshold": WATCHDOG_TIMEOUT_SECONDS},
        )).mappings().all()

    if not rows:
        return

    logger.warning("Watchdog: found %d stuck task(s)", len(rows))

    for row in rows:
        task_id = row["task_id"]
        user_id = row["user_id"] or ""
        source = row["source"] or "web"
        input_text = (row["input_text"] or "")[:80]
        callback_url = row.get("callback_url")
        age_min = row["age_sec"] // 60

        timeout_msg = timeout_msg_tpl.format(
            min=age_min or (WATCHDOG_TIMEOUT_SECONDS // 60),
            input=input_text + ("…" if len(row["input_text"] or "") > 80 else ""),
        )

        # 1. MySQL: 标记 timeout
        try:
            async with get_db() as db:
                await db.execute(
                    sa_text(
                        """
                        UPDATE claw_task_log
                        SET status = 'timeout',
                            error_message = :err,
                            timeout_at = NOW()
                        WHERE task_id = :tid AND status IN ('running', 'queued')
                        """
                    ),
                    {"tid": task_id, "err": f"Watchdog: stuck for {age_min}min"},
                )
            logger.info("Watchdog: marked %s as timeout (age=%dmin)", task_id, age_min)
        except Exception as exc:
            logger.error("Watchdog MySQL update failed for %s: %s", task_id, exc)
            continue

        # 2. WebSocket → 页面通知（定向给任务所有者）
        try:
            await manager.send_to_user(user_id, {
                "type": "task_timeout",
                "task_id": task_id,
                "message": timeout_msg,
                "age_min": age_min,
            })
        except Exception as exc:
            logger.debug("Watchdog WS notify failed for %s: %s", task_id, exc)

        # 3. Redis Pub/Sub → 广播进度事件（fallback for polling clients）
        try:
            r = await get_redis()
            await r.publish(rkey("task:progress"), json.dumps({
                "task_id": task_id,
                "step": "timeout",
                "detail": timeout_msg,
                "percent": 0,
                "worker_id": "watchdog",
                "ts": time.time(),
            }, ensure_ascii=False))
        except Exception as exc:
            logger.debug("Watchdog pubsub failed: %s", exc)

        # 4. callback_url → OpenClaw（飞书/CLI 主动推送）
        if callback_url:
            try:
                async with httpx.AsyncClient(timeout=10) as http:
                    await http.post(callback_url, json={
                        "task_id": task_id,
                        "status": "timeout",
                        "result_summary": timeout_msg,
                        "error_message": f"Watchdog: task stuck for {age_min}min",
                        "source": source,
                        "user_id": user_id,
                    })
                logger.info("Watchdog: callback_url notified for task %s", task_id)
            except Exception as exc:
                logger.warning("Watchdog callback_url POST failed for %s: %s", task_id, exc)


async def _pubsub_listener():
    """Subscribe to Redis task:progress channel; forward events to WebSocket clients."""
    logger.info("Pub/Sub progress listener started")
    r = await get_redis()
    # Create a dedicated connection for pub/sub (cannot reuse command connection)
    pubsub_conn = r.pubsub()
    try:
        await pubsub_conn.subscribe(rkey("task:progress"))
        while True:
            try:
                msg = await pubsub_conn.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if msg and msg.get("type") == "message":
                    try:
                        data = json.loads(msg["data"])
                        await manager.broadcast({"type": "task_progress", **data})
                    except Exception as exc:
                        logger.debug("Pub/Sub message parse error: %s", exc)
                else:
                    await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Pub/Sub listener error: %s", exc)
                await asyncio.sleep(1)
    except asyncio.CancelledError:
        logger.info("Pub/Sub progress listener stopped")
        try:
            await pubsub_conn.unsubscribe()
            await pubsub_conn.aclose()
        except Exception:
            pass


async def _queue_consumer(consumer_id: int = 0):
    """
    Background loop: BRPOP tasks from Redis queue and dispatch to idle workers.
    Multiple instances run concurrently; Redis BRPOP atomically prevents double-consumption.
    """
    logger.info("Queue consumer #%d started", consumer_id)
    try:
        while True:
            try:
                r = await get_redis()
                # BRPOP with 2s timeout (returns None if empty)
                result = await r.brpop(rkey("task_queue"), timeout=2)
                if result is None:
                    continue

                _, raw = result
                task = json.loads(raw)
                task_id = task.get("task_id", "unknown")

                # Classify intent if skill not set
                skill = task.get("skill", "")
                if not skill:
                    try:
                        from workers.skill_loader import classify_intent_from_config
                        skill = classify_intent_from_config(task.get("input_text", ""))
                        task["skill"] = skill
                        _qlog.intent_classified(task_id, task.get("input_text", ""), skill)
                    except Exception as exc:
                        logger.warning("Intent classification failed for task %s: %s", task_id, exc)
                        skill = "pi_agent"
                        task["skill"] = skill

                # Handle internal skills that don't need a worker
                if skill.startswith("__"):
                    logger.info("Skipping internal skill %s for task %s", skill, task_id)
                    continue

                # Broadcast "dispatching" status so frontend can update immediately
                await manager.broadcast({
                    "type": "task_progress",
                    "task_id": task_id,
                    "step": "dispatching",
                    "detail": f"分配给 {skill} 技能处理",
                    "percent": 3,
                })

                # Find an idle worker and dispatch
                from workers.pool import get_idle_worker
                worker = await get_idle_worker()

                if worker is not None:
                    try:
                        async with httpx.AsyncClient(timeout=10) as client:
                            resp = await client.post(
                                f"{worker['url']}/task",
                                json=task,
                            )
                            if resp.status_code == 200:
                                _qlog.dispatch(task_id, worker["worker_id"], skill)
                                logger.info(
                                    "Consumer#%d → dispatched task %s to %s (skill=%s)",
                                    consumer_id, task_id, worker["worker_id"], skill,
                                )
                                continue
                    except Exception as exc:
                        logger.warning("Dispatch to %s failed: %s", worker["worker_id"], exc)

                # No worker available — re-queue and back off
                # Use rpush to preserve FIFO order (re-queued task goes to the back)
                _qlog.requeue(task_id, "no idle worker")
                await r.rpush(rkey("task_queue"), raw)
                q_len = await r.llen(rkey("task_queue"))
                await manager.send_to_user(task.get("user_id", ""), {
                    "type": "task_queued",
                    "task_id": task_id,
                    "queue_position": q_len,
                    "message": f"所有 Worker 繁忙，重新排队（当前第 {q_len} 位）",
                })
                # Exponential backoff: avoid spinning; 1-4s depending on queue depth
                backoff = min(4.0, 0.5 * (1 + q_len // 5))
                await asyncio.sleep(backoff)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Queue consumer #%d error: %s", consumer_id, exc, exc_info=True)
                await asyncio.sleep(2)
    except asyncio.CancelledError:
        logger.info("Queue consumer #%d stopped", consumer_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    global _health_task, _pubsub_task, _scale_task, _watchdog_task, _queue_tasks

    # ── Startup ──
    logger.info("Starting OpenClaw TeamLab gateway on %s:%s", settings.HOST, settings.PORT)

    # Database connections
    try:
        await init_db()
    except Exception as exc:
        logger.error("Database init failed: %s", exc)

    # CoEvo prod DB (read-only)
    try:
        await init_coevo_db()
    except Exception as exc:
        logger.warning("CoEvo DB init failed (non-fatal): %s", exc)

    # Worker pool health monitor
    _health_task = asyncio.create_task(_worker_health_monitor())

    # Redis Pub/Sub → WebSocket progress forwarding
    _pubsub_task = asyncio.create_task(_pubsub_listener())

    # Auto-scale loop (runs every AUTO_SCALE_INTERVAL seconds)
    _scale_task = asyncio.create_task(_auto_scale_loop())

    # Task watchdog (scans stuck tasks every 60s)
    _watchdog_task = asyncio.create_task(_task_watchdog())

    # Start N parallel queue consumers (each does its own BRPOP — atomic, no duplicate)
    _queue_tasks = [
        asyncio.create_task(_queue_consumer(i))
        for i in range(CONCURRENT_CONSUMERS)
    ]
    logger.info(
        "Gateway ready: %d queue consumers, auto-scale every %ds, watchdog threshold=%ds",
        CONCURRENT_CONSUMERS, AUTO_SCALE_INTERVAL, WATCHDOG_TIMEOUT_SECONDS,
    )

    yield

    # ── Shutdown ──
    all_tasks = [_health_task, _pubsub_task, _scale_task, _watchdog_task, *_queue_tasks]
    for task in all_tasks:
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    await close_db()
    await close_coevo_db()
    logger.info("Gateway shutdown complete")


def create_app() -> FastAPI:
    """Application factory."""
    app = FastAPI(
        title="OpenClaw TeamLab",
        description="AI-powered research team management platform",
        version="0.1.0",
        lifespan=lifespan,
    )

    # ── CORS (permissive for dev) ──
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── API routes ──
    from gateway.routes.api import api_router
    app.include_router(api_router)

    # Agent API：在 app 层单独注册，确保 /api/agent/* 可用（OpenClaw 必需）
    try:
        from gateway.routes.agent_api import router as agent_router
        app.include_router(agent_router, prefix="/api")
        logger.info("Agent API (/api/agent/*) registered")
    except Exception as e:
        logger.warning("Agent API unavailable: %s — /api/agent/* will 404", e)

    # Knowledge API：知识图谱管理接口
    try:
        from gateway.routes.knowledge import router as knowledge_router
        app.include_router(knowledge_router)
        logger.info("Knowledge API (/api/knowledge/*) registered")
    except Exception as e:
        logger.warning("Knowledge API unavailable: %s — /api/knowledge/* will 404", e)

    # ── WebSocket ──
    @app.websocket("/ws")
    async def websocket_endpoint(
        websocket: WebSocket,
        client_id: str | None = None,
        user_id: str | None = None,
    ):
        cid = client_id or uuid4().hex[:12]
        await manager.connect(websocket, cid, user_id=user_id)
        try:
            while True:
                data = await websocket.receive_text()
                # Keepalive / client-side user_id registration
                try:
                    msg = json.loads(data)
                    if msg.get("type") == "register" and msg.get("user_id"):
                        manager._user_clients[msg["user_id"]].add(cid)
                except Exception:
                    pass
                await manager.send_to(cid, {"type": "ack", "data": data})
        except WebSocketDisconnect:
            manager.disconnect(cid)

    # ── Health check (Docker/K8s 探活) ──
    @app.get("/health")
    async def health():
        """Gateway health check. Returns 200 when service is up."""
        return {"status": "healthy", "service": "teamlab-gateway"}

    # ── Static files & SPA fallback ──
    web_dir = Path(settings.WEB_DIR)
    static_dir = web_dir / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    async def serve_index():
        index = web_dir / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return {"message": "OpenClaw TeamLab API is running. Web UI not built yet."}

    return app


app = create_app()
