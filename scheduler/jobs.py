"""
OpenClaw TeamLab — Scheduler Job Implementations
Task submission, worker health checks, and auto-scaling logic.
"""
import json
import logging
import uuid
from datetime import datetime

import httpx

from config.settings import settings
from config.database import get_redis, rkey
from config.log_setup import SchedulerLogger

logger = logging.getLogger("teamlab.scheduler.jobs")
_slog = SchedulerLogger()


async def run_skill_job(skill_name: str, params: dict | None = None):
    """
    Create a task payload and push it to the Redis task queue.
    Called by APScheduler for each configured cron job.

    Special skills starting with __ are executed directly, not via worker queue.
    """
    # Handle internal skills that run as direct async functions
    if skill_name == "__risk_compute__":
        await _run_risk_compute()
        return
    if skill_name == "__action_reconcile__":
        await _run_action_reconcile()
        return
    if skill_name == "__research_direction_analyze__":
        await _run_research_direction_analyze()
        return
    if skill_name == "__global_research_scan__":
        await _run_global_research_scan()
        return
    if skill_name == "__cross_project_analyze__":
        await _run_cross_project_analyze()
        return
    if skill_name == "__coevo_sync__":
        await _run_coevo_sync()
        return
    if skill_name == "__librarian__":
        await _run_librarian()
        return
    if skill_name == "__evolver__":
        await _run_evolver()
        return
    if skill_name == "__maintainer__":
        await _run_maintainer()
        return

    task_id = uuid.uuid4().hex
    payload = {
        "task_id": task_id,
        "user_id": "scheduler",
        "source": "scheduler",
        "skill": skill_name,
        "input_text": f"Scheduled execution of {skill_name}",
        "params": params or {},
        "created_at": datetime.utcnow().isoformat(),
    }

    try:
        r = await get_redis()
        await r.lpush(rkey("task_queue"), json.dumps(payload, ensure_ascii=False))
        logger.info(
            "Submitted scheduled task: skill=%s task_id=%s", skill_name, task_id
        )
        _slog.skill_dispatched(skill_name, skill_name, task_id)
    except Exception as exc:
        logger.error(
            "Failed to submit scheduled task %s: %s", skill_name, exc
        )
        _slog.job_error(skill_name, str(exc))


async def health_check_job():
    """
    Ping all workers registered in Redis.
    Mark workers as unhealthy after 3 consecutive failures.
    Log worker pool status summary.
    """
    try:
        r = await get_redis()
    except Exception as exc:
        logger.error("Health check: Redis unavailable: %s", exc)
        return

    all_workers = await r.hgetall(rkey("workers"))
    if not all_workers:
        logger.debug("Health check: no workers registered in Redis")
        return

    healthy = 0
    unhealthy = 0
    total = len(all_workers)

    for worker_id, raw in all_workers.items():
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            data = {}

        worker_url = data.get("url", f"http://127.0.0.1:{data.get('port', 0)}")
        fail_count = int(data.get("fail_count", 0))

        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{worker_url}/health")
                if resp.status_code == 200:
                    data["fail_count"] = 0
                    data["status"] = data.get("status", "idle")
                    healthy += 1
                    await r.hset(rkey("workers"), worker_id, json.dumps(data))
                    continue
        except Exception:
            pass

        # Failure path
        fail_count += 1
        data["fail_count"] = fail_count
        unhealthy_threshold = 3

        if fail_count >= unhealthy_threshold:
            data["status"] = "unhealthy"
            logger.warning(
                "Worker %s marked UNHEALTHY (%d consecutive failures)",
                worker_id,
                fail_count,
            )
            unhealthy += 1
        else:
            logger.debug(
                "Worker %s health check failed (%d/%d)",
                worker_id,
                fail_count,
                unhealthy_threshold,
            )

        await r.hset(rkey("workers"), worker_id, json.dumps(data))

    logger.info(
        "Health check complete: %d total, %d healthy, %d unhealthy",
        total,
        healthy,
        unhealthy,
    )


async def auto_scale_check():
    """
    Check worker pool utilization.
    If >80% busy, trigger pool scale-up via HTTP call to the gateway.
    """
    try:
        r = await get_redis()
    except Exception as exc:
        logger.error("Auto-scale check: Redis unavailable: %s", exc)
        return

    all_workers = await r.hgetall(rkey("workers"))
    total = len(all_workers)
    if total == 0:
        return

    busy_count = 0
    for raw in all_workers.values():
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            if data.get("status") == "busy":
                busy_count += 1
        except (json.JSONDecodeError, TypeError):
            pass

    busy_pct = busy_count / total
    queue_len = await r.llen(rkey("task_queue"))

    logger.debug(
        "Auto-scale: %d/%d workers busy (%.0f%%), queue_len=%d",
        busy_count,
        total,
        busy_pct * 100,
        queue_len,
    )

    if busy_pct > 0.8 or queue_len > total * 2:
        gateway_url = f"http://localhost:{settings.PORT}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{gateway_url}/api/system/scale-up")
                if resp.status_code == 200:
                    logger.info(
                        "Auto-scale triggered: busy_pct=%.0f%%, queue_len=%d",
                        busy_pct * 100,
                        queue_len,
                    )
                else:
                    logger.warning(
                        "Auto-scale request returned %d", resp.status_code
                    )
        except Exception as exc:
            logger.warning("Auto-scale HTTP call failed: %s", exc)


# ── Internal scheduled tasks (run directly, not via worker queue) ──

async def _run_risk_compute():
    """Execute risk score computation directly in the scheduler process."""
    import time
    start = time.time()
    _slog.job_start("__risk_compute__")
    try:
        from data_bridge.risk_engine import compute_all_risks
        from data_bridge.risk_alerts import send_risk_alerts
        logger.info("Running scheduled risk computation")
        results = await compute_all_risks()
        await send_risk_alerts(results)
        duration_ms = int((time.time() - start) * 1000)
        logger.info("Scheduled risk computation complete: %d claw_students", len(results))
        _slog.job_complete("__risk_compute__", duration_ms)
    except Exception as exc:
        logger.error("Scheduled risk computation failed: %s", exc, exc_info=True)
        _slog.job_error("__risk_compute__", str(exc))


async def _run_action_reconcile():
    """Execute action item reconciliation directly in the scheduler process."""
    import time
    start = time.time()
    _slog.job_start("__action_reconcile__")
    try:
        from data_bridge.action_tracker import reconcile_all_actions
        logger.info("Running scheduled action reconciliation")
        stats = await reconcile_all_actions()
        duration_ms = int((time.time() - start) * 1000)
        logger.info("Scheduled action reconciliation complete: %s", stats)
        _slog.job_complete("__action_reconcile__", duration_ms)
    except Exception as exc:
        logger.error("Scheduled action reconciliation failed: %s", exc, exc_info=True)
        _slog.job_error("__action_reconcile__", str(exc))


async def _run_research_direction_analyze():
    """Execute weekly research direction clustering in the scheduler process."""
    import time
    start = time.time()
    _slog.job_start("__research_direction_analyze__")
    try:
        from data_bridge.research_direction_analyzer import analyze_research_directions
        logger.info("Running scheduled research direction analysis")
        result = await analyze_research_directions()
        duration_ms = int((time.time() - start) * 1000)
        logger.info("Scheduled research direction analysis complete: %s", result)
        _slog.job_complete("__research_direction_analyze__", duration_ms)
    except Exception as exc:
        logger.error("Scheduled research direction analysis failed: %s", exc, exc_info=True)
        _slog.job_error("__research_direction_analyze__", str(exc))


async def _run_global_research_scan():
    """每日扫描全球 arxiv / Semantic Scholar 热点论文并生成洞见。"""
    import time
    start = time.time()
    _slog.job_start("__global_research_scan__")
    try:
        from data_bridge.global_research_monitor import scan_global_research
        logger.info("Running global research scan")
        result = await scan_global_research()
        duration_ms = int((time.time() - start) * 1000)
        logger.info("Global research scan complete: %s", result)
        _slog.job_complete("__global_research_scan__", duration_ms)
    except Exception as exc:
        logger.error("Global research scan failed: %s", exc, exc_info=True)
        _slog.job_error("__global_research_scan__", str(exc))


async def _run_cross_project_analyze():
    """每周分析跨项目协作机会并存储洞见。"""
    import time
    start = time.time()
    _slog.job_start("__cross_project_analyze__")
    try:
        from data_bridge.global_research_monitor import (
            analyze_cross_project_collaboration,
            _save_insight,
        )
        from datetime import date
        logger.info("Running cross-project collaboration analysis")
        insight = await analyze_cross_project_collaboration()
        if insight:
            await _save_insight(
                insight_type="cross_project",
                subject="跨项目协作机会",
                content=insight,
                metadata={"scan_date": date.today().isoformat()},
            )
        duration_ms = int((time.time() - start) * 1000)
        logger.info("Cross-project analysis complete (%.1fs)", duration_ms / 1000)
        _slog.job_complete("__cross_project_analyze__", duration_ms)
    except Exception as exc:
        logger.error("Cross-project analysis failed: %s", exc, exc_info=True)
        _slog.job_error("__cross_project_analyze__", str(exc))


async def _run_coevo_sync():
    """每4小时：从 cognalign_coevo_prod 增量同步最新团队数据到知识图谱。"""
    import time
    start = time.time()
    _slog.job_start("__coevo_sync__")
    try:
        from data_bridge.coevo_knowledge_sync import CoevoKnowledgeSync
        logger.info("Running coevo knowledge sync")
        result = await CoevoKnowledgeSync().run()
        duration_ms = int((time.time() - start) * 1000)
        logger.info(
            "CoevoSync complete: reports=%d plans=%d collabs=%d memories=%d total=%d (%.1fs)",
            result.get("reports", 0), result.get("plans", 0),
            result.get("collabs", 0), result.get("memories", 0),
            result.get("total_nodes", 0), duration_ms / 1000,
        )
        _slog.job_complete("__coevo_sync__", duration_ms)
    except Exception as exc:
        logger.error("CoevoSync job failed: %s", exc, exc_info=True)
        _slog.job_error("__coevo_sync__", str(exc))


async def _run_librarian():
    """每日：知识管理者从对话中提取团队知识，持续积累记忆。"""
    import time
    start = time.time()
    _slog.job_start("__librarian__")
    try:
        from roles.librarian import Librarian
        result = await Librarian().run()
        duration_ms = int((time.time() - start) * 1000)
        logger.info(
            "Librarian complete: processed=%d facts=%d (%.1fs)",
            result.get("processed", 0),
            result.get("facts_extracted", 0),
            duration_ms / 1000,
        )
        _slog.job_complete("__librarian__", duration_ms)
    except Exception as exc:
        logger.error("Librarian job failed: %s", exc, exc_info=True)
        _slog.job_error("__librarian__", str(exc))


async def _run_evolver():
    """每周：系统进化者分析运行数据，生成进化建议和健康报告。"""
    import time
    start = time.time()
    _slog.job_start("__evolver__")
    try:
        from roles.evolver import Evolver
        result = await Evolver().run()
        duration_ms = int((time.time() - start) * 1000)
        logger.info(
            "Evolver complete: suggestions=%d (%.1fs)",
            result.get("suggestions", 0),
            duration_ms / 1000,
        )
        _slog.job_complete("__evolver__", duration_ms)
    except Exception as exc:
        logger.error("Evolver job failed: %s", exc, exc_info=True)
        _slog.job_error("__evolver__", str(exc))


async def _run_maintainer():
    """每 15 分钟：系统维护者检查 Worker 健康、清理卡住任务、维护系统稳定性。"""
    import time
    start = time.time()
    _slog.job_start("__maintainer__")
    try:
        from roles.maintainer import Maintainer
        result = await Maintainer().run()
        duration_ms = int((time.time() - start) * 1000)
        logger.info(
            "Maintainer complete: workers=%d/%d healthy, stuck=%d cleared, queue=%d (%.1fs)",
            result.get("workers_healthy", 0),
            result.get("workers_total", 0),
            result.get("stuck_tasks_cleared", 0),
            result.get("queue_len", 0),
            duration_ms / 1000,
        )
        _slog.job_complete("__maintainer__", duration_ms)
    except Exception as exc:
        logger.error("Maintainer job failed: %s", exc, exc_info=True)
        _slog.job_error("__maintainer__", str(exc))
