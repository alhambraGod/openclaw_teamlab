"""
OpenClaw TeamLab — Task Result Poller
Polls MySQL claw_task_log for completed task results.
Used by IM receivers (Feishu/DingTalk) that dispatch tasks to workers
and need to wait for the actual result before replying.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from sqlalchemy import text

from config.database import get_db

logger = logging.getLogger(__name__)


async def poll_task_result(
    task_id: str,
    timeout: float = 120.0,
    poll_interval: float = 1.0,
) -> dict[str, Any]:
    """Poll claw_task_log until the task completes or times out.

    Returns:
        dict with keys: status, result_summary, result_data, error_message
    """
    elapsed = 0.0

    while elapsed < timeout:
        try:
            async with get_db() as session:
                row = await session.execute(
                    text(
                        "SELECT status, result_summary, result_data, error_message "
                        "FROM claw_task_log WHERE task_id = :tid LIMIT 1"
                    ),
                    {"tid": task_id},
                )
                result = row.mappings().first()

                if result and result["status"] in ("completed", "failed", "timeout"):
                    result_data = None
                    if result["result_data"]:
                        try:
                            result_data = json.loads(result["result_data"]) \
                                if isinstance(result["result_data"], str) \
                                else result["result_data"]
                        except (json.JSONDecodeError, TypeError):
                            result_data = {}

                    if result["status"] == "completed":
                        return {
                            "status": "ok",
                            "result": result["result_summary"] or "",
                            "data": result_data or {},
                        }
                    elif result["status"] == "timeout":
                        summary = result.get("result_summary") or ""
                        return {
                            "status": "timeout",
                            "error": result.get("error_message") or "Task timed out",
                            "result": summary if summary else (
                                "⏳ 这个问题的分析超时了（>3分钟），系统已自动回收资源。\n\n"
                                "💡 建议：稍后重试（已有缓存时会快很多），或拆解问题后重新提交。"
                            ),
                        }
                    else:
                        return {
                            "status": "error",
                            "error": result["error_message"] or "Task failed",
                        }
        except Exception as exc:
            logger.warning("[Poller] DB query error: %s", exc)

        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    return {
        "status": "timeout",
        "error": f"轮询等待超过 {int(timeout)}s，任务可能仍在运行。",
        "result": (
            "⏳ 等待任务结果超时，任务可能仍在后台运行。\n\n"
            "💡 建议：稍后通过 GET /api/chat/result/{task_id} 手动查询结果。"
        ),
    }
