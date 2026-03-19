"""
OpenClaw TeamLab — Maintainer Role（系统维护者）

职责：
  1. 监控所有 Worker 进程健康状态（HTTP ping、心跳检测）
  2. 发现并处理 stuck 任务（超时未完成 → 强制标记 timeout + 三通道通知）
  3. 自动清理 Redis 中的僵尸 Worker 注册（心跳过期）
  4. 统计系统整体健康指标并持久化到数据库
  5. 发现异常自动告警，生成健康报告供 PI 查阅

这是系统"自主运行"能力的基础保障：确保 Worker 池持续可用、任务不阻塞。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import text

from config.database import get_db, get_redis, rkey
from roles.base import AutonomousRole

logger = logging.getLogger("teamlab.roles.maintainer")

# Maintainer 判定超时阈值：比 Worker 侧 TASK_TIMEOUT_SECONDS(300) 多留容错
STUCK_TASK_THRESHOLD_SECONDS = 420  # 7 分钟
WORKER_STALE_HEARTBEAT_SECONDS = 90  # 心跳超过此时间无响应 → 视为僵尸


class Maintainer(AutonomousRole):
    """
    系统维护者：自动维护 Worker 池和任务生命周期。

    执行时机（APScheduler）：
      - 健康检查：每 5 分钟（__maintainer_health__）
      - 深度维护：每 15 分钟（__maintainer__）

    返回字典包含：
      workers_total, workers_healthy, workers_repaired,
      stuck_tasks_cleared, alerts_sent, summary
    """

    name = "maintainer"
    description = "系统维护者：Worker 健康守护 + 僵尸任务自动清理"

    async def run(self) -> dict[str, Any]:
        start = time.time()
        results: dict[str, Any] = {
            "workers_total": 0,
            "workers_healthy": 0,
            "workers_unhealthy": 0,
            "workers_stale_removed": 0,
            "stuck_tasks_cleared": 0,
            "queue_len": 0,
            "alerts": [],
            "summary": "",
        }

        # 1. Worker 健康检查 + 僵尸清理
        await self._check_workers(results)

        # 2. Stuck 任务强制清理
        await self._clear_stuck_tasks(results)

        # 3. 队列积压检测
        await self._check_queue(results)

        # 4. 持久化健康报告
        duration_ms = int((time.time() - start) * 1000)
        summary = self._build_summary(results, duration_ms)
        results["summary"] = summary
        results["duration_ms"] = duration_ms

        await self._save_health_report(results)

        logger.info(
            "Maintainer complete: workers=%d/%d healthy, stuck=%d cleared, queue=%d (%.1fs)",
            results["workers_healthy"],
            results["workers_total"],
            results["stuck_tasks_cleared"],
            results["queue_len"],
            duration_ms / 1000,
        )
        return results

    # ── Worker 检查 ────────────────────────────────────────────────────────────

    async def _check_workers(self, results: dict) -> None:
        """Ping 所有 Worker，标记不健康者，移除僵尸注册。"""
        try:
            r = await get_redis()
        except Exception as exc:
            logger.error("Maintainer: Redis unavailable: %s", exc)
            results["alerts"].append(f"Redis 不可用: {exc}")
            return

        all_workers = await r.hgetall(rkey("workers"))
        results["workers_total"] = len(all_workers)

        for wid, raw in all_workers.items():
            try:
                data = json.loads(raw) if isinstance(raw, str) else {}
            except (json.JSONDecodeError, TypeError):
                data = {}

            worker_url = data.get("url", f"http://127.0.0.1:{data.get('port', 0)}")
            alive_key = rkey(f"worker:alive:{wid}")

            # 检查心跳 key 是否存活
            alive = await r.exists(alive_key)
            if not alive:
                # 心跳 key 过期 → 判定为僵尸，移除注册
                await r.hdel(rkey("workers"), wid)
                results["workers_stale_removed"] += 1
                results["alerts"].append(f"Worker {wid} 心跳过期，已从 Redis 移除")
                logger.warning("Maintainer: removed stale worker %s (heartbeat expired)", wid)
                continue

            # HTTP ping 健康检查
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(f"{worker_url}/health")
                    if resp.status_code == 200:
                        health_data = resp.json()
                        # 更新 Redis 中的健康信息
                        data["status"] = data.get("status", "idle")
                        data["fail_count"] = 0
                        data["last_health_check"] = datetime.now(timezone.utc).isoformat()
                        await r.hset(rkey("workers"), wid, json.dumps(data))
                        results["workers_healthy"] += 1
                        continue
            except Exception as exc:
                logger.debug("Maintainer: worker %s ping failed: %s", wid, exc)

            # Ping 失败
            fail_count = int(data.get("fail_count", 0)) + 1
            data["fail_count"] = fail_count
            if fail_count >= 3:
                data["status"] = "unhealthy"
                results["workers_unhealthy"] += 1
                results["alerts"].append(
                    f"Worker {wid} 连续 {fail_count} 次 ping 失败，状态标记为 unhealthy"
                )
                logger.warning("Maintainer: worker %s unhealthy (%d failures)", wid, fail_count)
            await r.hset(rkey("workers"), wid, json.dumps(data))

    # ── Stuck 任务清理 ──────────────────────────────────────────────────────────

    async def _clear_stuck_tasks(self, results: dict) -> None:
        """
        扫描 MySQL 中超时未完成的 running/queued 任务：
        - 强制更新 status = 'timeout'
        - 通过 Redis Pub/Sub + callback_url 三通道通知用户
        """
        try:
            r = await get_redis()
        except Exception as exc:
            logger.error("Maintainer: Redis not available for stuck task cleanup: %s", exc)
            return

        timeout_msg_tpl = (
            "⏰ 您的任务已执行超时（>{min}分钟），维护者已自动终止并清理。\n"
            "原始问题：{input}\n"
            "请稍后重试，或将问题拆解后重新提交。"
        )

        try:
            async with get_db() as db:
                rows = (await db.execute(
                    text(
                        """
                        SELECT task_id, user_id, source, input_text, callback_url,
                               TIMESTAMPDIFF(SECOND, created_at, NOW()) AS age_sec
                        FROM claw_task_log
                        WHERE status IN ('running', 'queued')
                          AND created_at < DATE_SUB(NOW(), INTERVAL :threshold SECOND)
                        LIMIT 30
                        """
                    ),
                    {"threshold": STUCK_TASK_THRESHOLD_SECONDS},
                )).mappings().all()
        except Exception as exc:
            logger.error("Maintainer: DB query for stuck tasks failed: %s", exc)
            return

        if not rows:
            return

        logger.warning("Maintainer: found %d stuck task(s) to clear", len(rows))

        for row in rows:
            task_id = row["task_id"]
            user_id = row["user_id"] or ""
            source = row["source"] or "web"
            input_text = (row["input_text"] or "")[:80]
            callback_url = row.get("callback_url")
            age_min = max(1, row["age_sec"] // 60)

            timeout_msg = timeout_msg_tpl.format(
                min=age_min,
                input=input_text + ("…" if len(row["input_text"] or "") > 80 else ""),
            )

            # MySQL 标记 timeout
            try:
                async with get_db() as db:
                    updated = (await db.execute(
                        text(
                            """
                            UPDATE claw_task_log
                            SET status = 'timeout',
                                error_message = :err,
                                timeout_at = NOW()
                            WHERE task_id = :tid AND status IN ('running', 'queued')
                            """
                        ),
                        {
                            "tid": task_id,
                            "err": f"Maintainer: stuck for {age_min}min",
                        },
                    )).rowcount
                    if not updated:
                        continue  # 已被其他机制处理
                logger.info("Maintainer: cleared stuck task %s (age=%dmin)", task_id, age_min)
                results["stuck_tasks_cleared"] += 1
                results["alerts"].append(f"任务 {task_id[:12]}... 超时 {age_min} 分钟，已自动终止")
            except Exception as exc:
                logger.error("Maintainer: DB update failed for task %s: %s", task_id, exc)
                continue

            # Redis Pub/Sub → WebSocket → 页面
            try:
                await r.publish(rkey("task:progress"), json.dumps({
                    "task_id": task_id,
                    "step": "timeout",
                    "detail": timeout_msg,
                    "percent": 0,
                    "worker_id": "maintainer",
                    "ts": time.time(),
                }, ensure_ascii=False))
            except Exception as exc:
                logger.debug("Maintainer pubsub notify failed: %s", exc)

            # callback_url → OpenClaw（飞书/CLI）
            if callback_url:
                try:
                    async with httpx.AsyncClient(timeout=10) as http:
                        await http.post(callback_url, json={
                            "task_id": task_id,
                            "status": "timeout",
                            "result_summary": timeout_msg,
                            "error_message": f"Maintainer: task stuck for {age_min}min",
                            "source": source,
                            "user_id": user_id,
                        })
                except Exception as exc:
                    logger.debug("Maintainer callback_url failed for %s: %s", task_id, exc)

    # ── 队列积压检测 ────────────────────────────────────────────────────────────

    async def _check_queue(self, results: dict) -> None:
        """检查 Redis 任务队列积压情况，超过阈值告警。"""
        try:
            r = await get_redis()
            q_len = await r.llen(rkey("task_queue"))
            results["queue_len"] = q_len
            if q_len > 20:
                results["alerts"].append(
                    f"⚠️ 任务队列积压 {q_len} 条，请检查 Worker 是否正常"
                )
                logger.warning("Maintainer: task queue backlog = %d", q_len)
        except Exception as exc:
            logger.debug("Maintainer queue check failed: %s", exc)

    # ── 健康报告持久化 ──────────────────────────────────────────────────────────

    def _build_summary(self, results: dict, duration_ms: int) -> str:
        workers_ok = results["workers_healthy"]
        workers_total = results["workers_total"]
        stuck = results["stuck_tasks_cleared"]
        stale = results["workers_stale_removed"]
        q_len = results["queue_len"]
        alerts = results["alerts"]

        status_icon = "✅" if not alerts else "⚠️"
        lines = [
            f"{status_icon} **系统维护报告** ({datetime.now().strftime('%Y-%m-%d %H:%M')})",
            f"",
            f"**Worker 池状态**",
            f"- 在线: {workers_ok}/{workers_total}  |  移除僵尸: {stale}",
            f"- 队列积压: {q_len} 条",
            f"",
            f"**任务管理**",
            f"- 清理卡住任务: {stuck} 条",
        ]
        if alerts:
            lines.append(f"")
            lines.append(f"**告警事项**")
            for alert in alerts[:5]:
                lines.append(f"- {alert}")
            if len(alerts) > 5:
                lines.append(f"- ...共 {len(alerts)} 条")
        else:
            lines.append(f"- 无异常告警 🎉")

        lines.append(f"")
        lines.append(f"*执行耗时: {duration_ms}ms*")
        return "\n".join(lines)

    async def _save_health_report(self, results: dict) -> None:
        """将健康报告持久化到 claw_pi_agent_insights。"""
        try:
            await self.save_insight(
                insight_type="system_health",
                subject="系统维护报告",
                content=results["summary"],
                metadata={
                    "workers_total": results["workers_total"],
                    "workers_healthy": results["workers_healthy"],
                    "workers_stale_removed": results["workers_stale_removed"],
                    "stuck_tasks_cleared": results["stuck_tasks_cleared"],
                    "queue_len": results["queue_len"],
                    "alert_count": len(results["alerts"]),
                    "duration_ms": results.get("duration_ms", 0),
                },
            )
        except Exception as exc:
            logger.warning("Maintainer: failed to save health report: %s", exc)
