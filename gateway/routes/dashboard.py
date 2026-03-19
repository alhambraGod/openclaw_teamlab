"""
OpenClaw TeamLab — Dashboard Routes
Overview stats and recent activity for the PI dashboard.
"""
import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from config.database import get_db, get_redis, rkey
from models import Student, ProgressEvent, ResearchDirection, TaskLog

logger = logging.getLogger("teamlab.routes.dashboard")
router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/overview")
async def dashboard_overview():
    """Team stats: merged CoEvo prod stats + openclaw data + system status."""
    try:
        # ── CoEvo prod stats (real team data) ──
        coevo_stats = {}
        try:
            from data_bridge import queries as Q
            coevo_stats = await Q.get_project_stats()
        except Exception as exc:
            logger.warning("CoEvo stats unavailable: %s", exc)

        async with get_db() as db:
            # OpenClaw student counts
            oc_rows = (await db.execute(
                select(Student.status, func.count(Student.id)).group_by(Student.status)
            )).all()
            oc_counts = {status: count for status, count in oc_rows}
            oc_active = oc_counts.get("active", 0)

            active_students = max(
                oc_active,
                coevo_stats.get("role_distribution", {}).get("student", 0)
                + coevo_stats.get("role_distribution", {}).get("researcher", 0),
            )

            # Recent events
            events_result = (await db.execute(
                select(ProgressEvent)
                .order_by(ProgressEvent.event_date.desc())
                .limit(10)
            )).scalars().all()
            recent_events = [
                {
                    "id": e.id,
                    "student_id": e.student_id,
                    "event_type": e.event_type,
                    "title": e.title,
                    "event_date": str(e.event_date),
                }
                for e in events_result
            ]

            active_dirs = (await db.execute(
                select(func.count(ResearchDirection.id)).where(
                    ResearchDirection.status.in_(["active", "exploring"])
                )
            )).scalar() or 0

        # System status from Redis
        system_ok = True
        queue_len = 0
        busy_workers = 0
        total_workers = 0
        try:
            r = await get_redis()
            queue_len = await r.llen(rkey("task_queue"))
            worker_keys = await r.keys(rkey("worker:*:heartbeat"))
            total_workers = len(worker_keys)
        except Exception as exc:
            logger.warning("Redis status check failed: %s", exc)
            system_ok = False

        return {
            "active_students": active_students,
            "total_projects": coevo_stats.get("total_projects", 0),
            "total_meetings": coevo_stats.get("total_meetings", 0),
            "completed_meetings": coevo_stats.get("completed_meetings", 0),
            "active_directions": active_dirs,
            "pending_papers": 0,
            "weekly_tasks": queue_len,
            "coevo_role_distribution": coevo_stats.get("role_distribution", {}),
            "recent_events": recent_events,
            "ai_insights": [],
            "system_ok": system_ok,
            "busy_workers": busy_workers,
            "total_workers": total_workers,
        }
    except Exception as exc:
        logger.error("Dashboard overview error: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to load dashboard overview")


@router.get("/activity")
async def dashboard_activity():
    """Recent claw_task_log entries."""
    try:
        async with get_db() as db:
            result = (await db.execute(
                select(TaskLog)
                .order_by(TaskLog.created_at.desc())
                .limit(50)
            )).scalars().all()

            return [
                {
                    "id": t.id,
                    "task_id": t.task_id,
                    "user_id": t.user_id,
                    "source": t.source,
                    "skill_used": t.skill_used,
                    "input_text": t.input_text,
                    "status": t.status,
                    "duration_ms": t.duration_ms,
                    "created_at": str(t.created_at) if t.created_at else None,
                    "completed_at": str(t.completed_at) if t.completed_at else None,
                }
                for t in result
            ]
    except Exception as exc:
        logger.error("Dashboard activity error: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to load activity")


@router.get("/projects-overview")
async def projects_overview():
    """Per-project stats for the redesigned dashboard: name, member_count, completed_meetings."""
    try:
        from data_bridge import queries as Q
        from models.coevo import CoevoMeeting
        from config.coevo_db import get_coevo_db
        from sqlalchemy import select, func

        projects = await Q.get_all_projects()
        async with get_coevo_db() as db:
            # Count completed claw_meetings per project
            rows = (await db.execute(
                select(CoevoMeeting.project_id, func.count(CoevoMeeting.id).label("cnt"))
                .where(CoevoMeeting.status == "completed")
                .group_by(CoevoMeeting.project_id)
            )).all()
        completed_map = {r.project_id: r.cnt for r in rows}

        result = []
        for p in projects:
            result.append({
                "id": p["id"],
                "name": p["project_name"],
                "member_count": p["member_count"] or 0,
                "completed_meetings": completed_map.get(p["id"], 0),
            })
        result.sort(key=lambda x: -x["completed_meetings"])
        return result
    except Exception as exc:
        logger.error("projects_overview error: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to load projects overview")
