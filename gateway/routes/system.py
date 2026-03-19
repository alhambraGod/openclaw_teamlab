"""
OpenClaw TeamLab — System Routes
Worker status, PI configuration, scheduler management, and database management.
"""
import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text

from config.database import get_db, get_redis, rkey, engine
from config.settings import settings
from models import PiConfig, Base

logger = logging.getLogger("teamlab.routes.system")
router = APIRouter(prefix="/system", tags=["system"])

_start_time = time.time()

# Scheduler is on a separate port (10302 by default)
def _scheduler_url(path: str) -> str:
    return f"http://127.0.0.1:{settings.SCHEDULER_PORT}{path}"


class ConfigUpdate(BaseModel):
    value: Any
    description: str | None = None


class CronUpdate(BaseModel):
    cron: str
    description: str | None = None


# ── Routes ──

@router.get("/status")
async def system_status():
    """Worker pool status, queue length, and gateway uptime."""
    uptime_seconds = int(time.time() - _start_time)

    status = {
        "gateway": "online",
        "uptime_seconds": uptime_seconds,
        "host": settings.HOST,
        "port": settings.PORT,
        "env": settings.ENV,
    }

    # Redis queue info
    try:
        r = await get_redis()
        queue_len = await r.llen(rkey("task_queue"))
        status["queue_length"] = queue_len
        status["redis"] = "connected"
    except Exception as exc:
        logger.warning("Redis status check failed: %s", exc)
        status["redis"] = "unavailable"
        status["queue_length"] = None

    # Probe workers via HTTP health endpoints
    workers = []
    async with httpx.AsyncClient(timeout=2.0) as client:
        for i in range(settings.WORKER_MAX):
            port = settings.WORKER_PORT_BASE + i
            try:
                resp = await client.get(f"http://127.0.0.1:{port}/health")
                if resp.status_code == 200:
                    data = resp.json()
                    workers.append({
                        "id": data.get("worker_id", f"worker-{i}"),
                        "port": port,
                        "status": data.get("status", "unknown"),
                    })
            except httpx.ConnectError:
                break  # No more workers beyond this port
            except Exception:
                continue

    status["workers"] = workers
    status["active_workers"] = len(workers)

    return status


@router.get("/config")
async def get_config():
    """Read all PI configuration entries."""
    async with get_db() as db:
        result = (await db.execute(
            select(PiConfig).order_by(PiConfig.config_key)
        )).scalars().all()
        return [
            {
                "id": c.id,
                "key": c.config_key,
                "value": c.config_value,
                "description": c.description,
                "updated_at": str(c.updated_at) if c.updated_at else None,
            }
            for c in result
        ]


@router.put("/config/{key}")
async def update_config(key: str, body: ConfigUpdate):
    """Create or update a PI configuration entry."""
    async with get_db() as db:
        config = (await db.execute(
            select(PiConfig).where(PiConfig.config_key == key)
        )).scalar_one_or_none()

        if config:
            config.config_value = body.value
            if body.description is not None:
                config.description = body.description
        else:
            config = PiConfig(
                config_key=key,
                config_value=body.value,
                description=body.description,
            )
            db.add(config)

        await db.flush()
        await db.refresh(config)
        return {
            "id": config.id,
            "key": config.config_key,
            "value": config.config_value,
            "description": config.description,
            "updated_at": str(config.updated_at) if config.updated_at else None,
        }


@router.post("/init-db")
async def init_database():
    """Run database migrations — create all tables from ORM models."""
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return {"status": "ok", "message": "All tables created/verified"}
    except Exception as exc:
        logger.error("Database init failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Migration failed: {exc}")


# ── Scheduler Management (proxy to port 10302) ──────────────────────────────

@router.get("/scheduler/health")
async def scheduler_health():
    """Scheduler service health and uptime."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(_scheduler_url("/health"))
            return resp.json()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Scheduler unreachable: {exc}")


@router.get("/scheduler/jobs")
async def scheduler_list_jobs():
    """List all scheduled jobs with cron, description, and next run time."""
    import yaml
    from pathlib import Path

    # Read job metadata (cron, description, skill) from agents.yaml
    config_path = Path(settings.PROJECT_ROOT) / "config" / "agents.yaml"
    job_meta: dict[str, dict] = {}
    if config_path.exists():
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        for j in cfg.get("scheduler", {}).get("jobs", []):
            job_meta[j["id"]] = {
                "cron": j.get("cron", ""),
                "description": j.get("description", ""),
                "skill": j.get("skill", j["id"]),
            }

    # Get live next_run_time from scheduler process
    scheduler_jobs: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(_scheduler_url("/jobs"))
            for j in resp.json():
                scheduler_jobs[j["id"]] = j
        except Exception:
            pass  # Scheduler may be unreachable; return yaml-only data

    # Merge
    result = []
    for job_id, meta in job_meta.items():
        live = scheduler_jobs.get(job_id, {})
        result.append({
            "id": job_id,
            "cron": meta["cron"],
            "description": meta["description"],
            "skill": meta["skill"],
            "next_run_time": live.get("next_run_time"),
            "trigger": live.get("trigger"),
            "paused": live.get("next_run_time") is None and job_id in scheduler_jobs,
        })

    # Also include internal jobs (health_check, auto_scale_check) not in yaml
    for job_id, live in scheduler_jobs.items():
        if job_id not in job_meta:
            result.append({
                "id": job_id,
                "cron": live.get("trigger", ""),
                "description": live.get("name", job_id),
                "skill": "",
                "next_run_time": live.get("next_run_time"),
                "trigger": live.get("trigger"),
                "internal": True,
            })

    return result


@router.put("/scheduler/jobs/{job_id}")
async def update_scheduler_job(job_id: str, body: CronUpdate):
    """Update a job's cron expression. Changes are persisted to agents.yaml."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.put(
                _scheduler_url(f"/jobs/{job_id}"),
                json={"cron": body.cron, "description": body.description},
            )
            if resp.status_code == 404:
                raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
            if resp.status_code == 400:
                raise HTTPException(status_code=400, detail=resp.json().get("detail", "Invalid cron"))
            resp.raise_for_status()
            return resp.json()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Scheduler unreachable: {exc}")


@router.post("/scheduler/jobs/{job_id}/trigger")
async def trigger_scheduler_job(job_id: str):
    """Manually trigger a scheduled job immediately."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(_scheduler_url(f"/trigger/{job_id}"))
            if resp.status_code == 404:
                raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
            resp.raise_for_status()
            return resp.json()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Scheduler unreachable: {exc}")


@router.post("/scheduler/jobs/{job_id}/pause")
async def pause_scheduler_job(job_id: str):
    """Pause a scheduled job."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.delete(_scheduler_url(f"/jobs/{job_id}/pause"))
            if resp.status_code == 404:
                raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
            resp.raise_for_status()
            return resp.json()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Scheduler unreachable: {exc}")


@router.post("/scheduler/jobs/{job_id}/resume")
async def resume_scheduler_job(job_id: str):
    """Resume a paused scheduled job."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.post(_scheduler_url(f"/jobs/{job_id}/resume"))
            if resp.status_code == 404:
                raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
            resp.raise_for_status()
            return resp.json()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Scheduler unreachable: {exc}")


# ── CoEvo 数据同步 ────────────────────────────────────────────────────────────

@router.post("/coevo/sync")
async def trigger_coevo_sync():
    """
    手动触发 cognalign_coevo_prod → 知识图谱的增量同步。
    会在后台异步执行，立即返回 accepted。
    适用场景：刚完成会议、更新研究规划后希望立即同步最新数据。
    """
    import asyncio
    from data_bridge.coevo_knowledge_sync import CoevoKnowledgeSync

    async def _do_sync():
        try:
            result = await CoevoKnowledgeSync().run()
            logger.info("Manual coevo sync complete: %s", result)
        except Exception as exc:
            logger.error("Manual coevo sync failed: %s", exc, exc_info=True)

    asyncio.create_task(_do_sync())
    return {"status": "accepted", "message": "CoEvo 数据同步已在后台启动，约30-60秒完成"}


@router.get("/coevo/sync/status")
async def coevo_sync_status():
    """查询各数据类型的同步水印（最后同步时间）。"""
    from config.database import get_redis, rkey
    wm_keys = {
        "meeting_reports": "coevo_wm:meeting_reports",
        "research_plans":  "coevo_wm:research_plans",
        "collab_recs":     "coevo_wm:collab_recs",
        "agent_memories":  "coevo_wm:agent_memories",
    }
    result: dict = {}
    try:
        r = await get_redis()
        for label, key in wm_keys.items():
            val = await r.get(rkey(key))
            result[label] = (val.decode() if isinstance(val, bytes) else str(val)) if val else None
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {exc}")
    return {"watermarks": result, "note": "None 表示尚未同步（首次运行将拉取最近30天数据）"}


@router.delete("/coevo/sync/watermarks")
async def reset_coevo_watermarks(data_type: str | None = None):
    """
    重置 CoEvo 同步水印，下次同步将重新拉取指定类型（或全部）的数据。
    参数 data_type 可选：meeting_reports | research_plans | collab_recs | agent_memories
    """
    from config.database import get_redis, rkey
    wm_map = {
        "meeting_reports": "coevo_wm:meeting_reports",
        "research_plans":  "coevo_wm:research_plans",
        "collab_recs":     "coevo_wm:collab_recs",
        "agent_memories":  "coevo_wm:agent_memories",
    }
    if data_type and data_type not in wm_map:
        raise HTTPException(status_code=400, detail=f"Unknown data_type: {data_type}. Valid: {list(wm_map)}")

    keys_to_reset = [wm_map[data_type]] if data_type else list(wm_map.values())
    try:
        r = await get_redis()
        for k in keys_to_reset:
            await r.delete(rkey(k))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {exc}")

    return {
        "status": "ok",
        "reset": [data_type] if data_type else list(wm_map.keys()),
        "message": "水印已清除，下次 coevo_sync 将重新拉取历史数据（最近30天）",
    }
