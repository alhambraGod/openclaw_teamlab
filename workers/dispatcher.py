"""
OpenClaw TeamLab — Task Dispatcher
Routes incoming tasks to idle workers or queues them in Redis.
Called by Gateway routes and Feishu receiver.
"""
import json
import logging
import uuid

import httpx

from config.database import get_redis, rkey
from workers.pool import get_idle_worker

logger = logging.getLogger("teamlab.dispatcher")


async def dispatch_task(task_payload: dict) -> dict:
    """
    Dispatch a task to an available worker or queue it.

    Args:
        task_payload: dict with keys:
            - user_id (str)
            - source (str): "feishu", "web", "api", or "scheduler"
            - skill (str): skill name
            - input_text (str): user input

    Returns:
        dict with task_id and status ("dispatched" or "queued").
    """
    task_id = task_payload.get("task_id") or uuid.uuid4().hex
    task_payload["task_id"] = task_id

    # Try to find an idle worker
    worker = await get_idle_worker()

    if worker is not None:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{worker['url']}/task",
                    json=task_payload,
                )
                if resp.status_code == 200:
                    logger.info(
                        "Task %s dispatched to %s (skill=%s)",
                        task_id, worker["worker_id"], task_payload.get("skill"),
                    )
                    return {
                        "task_id": task_id,
                        "status": "dispatched",
                        "worker_id": worker["worker_id"],
                    }
                else:
                    logger.warning(
                        "Worker %s rejected task %s (status=%d), queueing",
                        worker["worker_id"], task_id, resp.status_code,
                    )
        except Exception as exc:
            logger.warning(
                "Failed to dispatch task %s to worker %s: %s — queueing",
                task_id, worker["worker_id"], exc,
            )

    # No idle worker available or dispatch failed — queue in Redis
    r = await get_redis()
    await r.lpush(rkey("task_queue"), json.dumps(task_payload))
    logger.info("Task %s queued (skill=%s)", task_id, task_payload.get("skill"))

    return {
        "task_id": task_id,
        "status": "queued",
    }
