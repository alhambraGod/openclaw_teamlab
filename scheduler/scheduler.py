"""
OpenClaw TeamLab — APScheduler-based Job Runner
Loads job definitions from config/agents.yaml and manages scheduled execution.
"""
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config.settings import settings
from config.log_setup import SchedulerLogger
from scheduler.jobs import run_skill_job, health_check_job, auto_scale_check

logger = logging.getLogger("teamlab.scheduler")
_slog = SchedulerLogger()

# ── Module State ──
_scheduler: AsyncIOScheduler | None = None
_start_time: float | None = None


def _load_job_definitions() -> list[dict]:
    """Load scheduler job definitions from config/agents.yaml."""
    config_path = settings.PROJECT_ROOT / "config" / "agents.yaml"
    if not config_path.exists():
        logger.warning("agents.yaml not found at %s", config_path)
        return []

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    return config.get("scheduler", {}).get("jobs", [])


def _parse_cron(cron_expr: str) -> CronTrigger:
    """Parse a 5-field cron expression into an APScheduler CronTrigger."""
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Expected 5-field cron expression, got: {cron_expr}")

    minute, hour, day, month, day_of_week = parts
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=day_of_week,
    )


def _persist_cron_update(job_id: str, cron: str, description: str | None = None) -> None:
    """Write updated cron expression back to config/agents.yaml."""
    config_path = settings.PROJECT_ROOT / "config" / "agents.yaml"
    if not config_path.exists():
        logger.warning("agents.yaml not found, skipping persist for job %s", job_id)
        return

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    jobs = config.get("scheduler", {}).get("jobs", [])
    for job in jobs:
        if job["id"] == job_id:
            job["cron"] = cron
            if description is not None:
                job["description"] = description
            break

    with open(config_path, "w") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    logger.info("Persisted cron update for job %s: %s", job_id, cron)


def build_scheduler() -> AsyncIOScheduler:
    """Create and configure the APScheduler instance with all jobs."""
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

    # ── Internal: health check every 30 seconds ──
    scheduler.add_job(
        health_check_job,
        trigger=IntervalTrigger(seconds=30),
        id="health_check",
        name="Worker Health Check",
        replace_existing=True,
    )

    # ── Internal: auto-scale check every 60 seconds ──
    scheduler.add_job(
        auto_scale_check,
        trigger=IntervalTrigger(seconds=60),
        id="auto_scale_check",
        name="Auto Scale Check",
        replace_existing=True,
    )

    # ── Internal: maintainer（系统维护者）每 15 分钟深度巡检 ──
    # 检查 Worker 健康、清理 stuck 任务、维护 Redis 注册表
    scheduler.add_job(
        lambda: run_skill_job("__maintainer__"),
        trigger=IntervalTrigger(minutes=15),
        id="maintainer",
        name="系统维护者 (Worker健康+任务清理)",
        replace_existing=True,
    )

    # ── Load jobs from config/agents.yaml ──
    job_defs = _load_job_definitions()
    for job_def in job_defs:
        job_id = job_def["id"]
        cron_expr = job_def["cron"]
        skill_name = job_def.get("skill", job_id)
        description = job_def.get("description", "")
        params = job_def.get("params")

        try:
            trigger = _parse_cron(cron_expr)
        except ValueError as exc:
            logger.error("Invalid cron for job %s: %s", job_id, exc)
            continue

        scheduler.add_job(
            run_skill_job,
            trigger=trigger,
            id=job_id,
            name=description or job_id,
            args=[skill_name],
            kwargs={"params": params},
            replace_existing=True,
        )
        logger.info("Registered job: %s [%s] -> skill=%s", job_id, cron_expr, skill_name)
        _slog.job_trigger(job_id, cron_expr)

    return scheduler


# ── FastAPI Control Server ──

def create_scheduler_app() -> FastAPI:
    """Build the FastAPI app that exposes scheduler management endpoints."""
    import time

    app = FastAPI(
        title="OpenClaw TeamLab Scheduler",
        version="0.1.0",
    )

    @app.on_event("startup")
    async def startup():
        global _scheduler, _start_time
        _start_time = time.time()
        _scheduler = build_scheduler()
        _scheduler.start()
        logger.info(
            "Scheduler started with %d jobs on port %d",
            len(_scheduler.get_jobs()),
            settings.SCHEDULER_PORT,
        )

    @app.on_event("shutdown")
    async def shutdown():
        global _scheduler
        if _scheduler:
            _scheduler.shutdown(wait=False)
            logger.info("Scheduler shut down")

    @app.get("/health")
    async def health():
        """Scheduler health and uptime."""
        running = _scheduler is not None and _scheduler.running
        uptime = int(time.time() - _start_time) if _start_time else 0
        return {
            "status": "running" if running else "stopped",
            "uptime_seconds": uptime,
            "job_count": len(_scheduler.get_jobs()) if _scheduler else 0,
        }

    @app.get("/jobs")
    async def list_jobs():
        """List all scheduled jobs with next run times."""
        if _scheduler is None:
            return []

        jobs = []
        for job in _scheduler.get_jobs():
            next_run = job.next_run_time
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run_time": next_run.isoformat() if next_run else None,
                "trigger": str(job.trigger),
            })
        return jobs

    @app.post("/trigger/{job_id}")
    async def trigger_job(job_id: str):
        """Manually trigger a scheduled job immediately."""
        if _scheduler is None:
            raise HTTPException(status_code=503, detail="Scheduler not running")

        job = _scheduler.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

        try:
            result = job.func(*job.args, **job.kwargs)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logger.error("Manual trigger of %s failed: %s", job_id, exc)
            raise HTTPException(status_code=500, detail=str(exc))

        return {"status": "triggered", "job_id": job_id}

    class CronUpdate(BaseModel):
        cron: str
        description: str | None = None

    @app.put("/jobs/{job_id}")
    async def update_job_cron(job_id: str, body: CronUpdate):
        """Update cron expression for a scheduled job and persist to agents.yaml."""
        if _scheduler is None:
            raise HTTPException(status_code=503, detail="Scheduler not running")

        job = _scheduler.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

        # Validate cron expression
        try:
            new_trigger = _parse_cron(body.cron)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        # Reschedule in-memory
        _scheduler.reschedule_job(job_id, trigger=new_trigger)
        logger.info("Rescheduled job %s with new cron: %s", job_id, body.cron)

        # Persist to agents.yaml
        _persist_cron_update(job_id, body.cron, body.description)

        updated_job = _scheduler.get_job(job_id)
        next_run = updated_job.next_run_time if updated_job else None
        return {
            "status": "updated",
            "job_id": job_id,
            "cron": body.cron,
            "next_run_time": next_run.isoformat() if next_run else None,
        }

    @app.delete("/jobs/{job_id}/pause")
    async def pause_job(job_id: str):
        """Pause a scheduled job."""
        if _scheduler is None:
            raise HTTPException(status_code=503, detail="Scheduler not running")
        job = _scheduler.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
        _scheduler.pause_job(job_id)
        return {"status": "paused", "job_id": job_id}

    @app.post("/jobs/{job_id}/resume")
    async def resume_job(job_id: str):
        """Resume a paused scheduled job."""
        if _scheduler is None:
            raise HTTPException(status_code=503, detail="Scheduler not running")
        job = _scheduler.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
        _scheduler.resume_job(job_id)
        updated = _scheduler.get_job(job_id)
        return {
            "status": "resumed",
            "job_id": job_id,
            "next_run_time": updated.next_run_time.isoformat() if updated and updated.next_run_time else None,
        }

    return app


def run_scheduler():
    """Entry point: start the scheduler's FastAPI server."""
    import uvicorn
    from config.log_setup import setup_logging
    setup_logging("scheduler")

    app = create_scheduler_app()
    uvicorn.run(
        app,
        host=settings.HOST,
        port=settings.SCHEDULER_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    run_scheduler()
