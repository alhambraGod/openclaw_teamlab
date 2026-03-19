"""
OpenClaw TeamLab — CoEvo Integration Routes
Provides read views into cognalign-coevo prod data enriched with openclaw analytics.
All coevo data is read-only; openclaw-generated insights are stored in openclaw_teamlab DB.

Route prefix: /api/coevo
"""
import logging
from datetime import date

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from sqlalchemy import select, func

from config.coevo_db import get_coevo_db
from config.database import get_db
from models import CoevoStudentLink, CoevoSyncLog, MeetingInsight, Student
from models.coevo import (
    CoevoUser, CoevoProject, CoevoProjectMember,
    CoevoMeeting, CoevoMeetingReport, CoevoCollabRecommendation, CoevoResearchPlan,
)
from data_bridge import queries as Q
from data_bridge.sync import sync_students

logger = logging.getLogger("teamlab.routes.coevo")
router = APIRouter(prefix="/coevo", tags=["coevo"])


# ── Members (for main Students tab) ───────────────────────────────────────────

@router.get("/members")
async def list_coevo_members(project_id: int | None = Query(None)):
    """Return all team members with project membership goals, mapped to student shape."""
    try:
        return await Q.get_members_with_goals(project_id=project_id)
    except Exception as exc:
        logger.error("List coevo members error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Collaborations (for main Collaborations tab) ──────────────────────────────

@router.get("/collaborations")
async def list_coevo_collaborations(limit: int = Query(100)):
    """Return all completed collaboration recommendations with resolved user names."""
    try:
        return await Q.get_all_collabs(limit=limit)
    except Exception as exc:
        logger.error("List coevo collaborations error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Research Plans (for main Directions tab) ─────────────────────────────────

@router.get("/research-plans")
async def list_coevo_research_plans(limit: int = Query(50)):
    """Return all completed CoEvo research plans mapped to direction shape."""
    try:
        return await Q.get_all_research_plans(limit=limit)
    except Exception as exc:
        logger.error("List coevo research plans error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Overview / Stats ──────────────────────────────────────────────────────────

@router.get("/overview")
async def coevo_overview():
    """
    Dashboard overview combining coevo prod stats with openclaw sync status.
    Shows real-time data from cognalign-coevo alongside openclaw enrichment counts.
    """
    try:
        # CoEvo stats
        coevo_stats = await Q.get_project_stats()

        # OpenClaw sync status
        async with get_db() as db:
            total_links = (await db.execute(
                select(func.count(CoevoStudentLink.id))
            )).scalar() or 0

            total_insights = (await db.execute(
                select(func.count(MeetingInsight.id))
            )).scalar() or 0

            last_sync = (await db.execute(
                select(CoevoSyncLog)
                .where(CoevoSyncLog.status == "completed")
                .order_by(CoevoSyncLog.completed_at.desc())
                .limit(1)
            )).scalar_one_or_none()

        return {
            "coevo_stats": coevo_stats,
            "openclaw_enrichment": {
                "synced_students": total_links,
                "claw_meeting_insights": total_insights,
                "last_sync_at": str(last_sync.completed_at) if last_sync else None,
                "last_sync_type": last_sync.sync_type if last_sync else None,
            },
        }
    except Exception as exc:
        logger.error("CoEvo overview error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Projects ──────────────────────────────────────────────────────────────────

@router.get("/projects")
async def list_coevo_projects():
    """List all active coevo projects with member counts."""
    try:
        projects = await Q.get_all_projects()

        # Enrich each project with openclaw research plan info
        async with get_coevo_db() as coevo_db:
            for p in projects:
                # Meetings count
                meeting_count = (await coevo_db.execute(
                    select(func.count(CoevoMeeting.id))
                    .where(
                        CoevoMeeting.project_id == p["id"],
                        CoevoMeeting.is_active == True,
                    )
                )).scalar() or 0
                p["meeting_count"] = meeting_count

                # Member count per role
                role_rows = (await coevo_db.execute(
                    select(CoevoProjectMember.project_role, func.count(CoevoProjectMember.id))
                    .where(CoevoProjectMember.project_id == p["id"])
                    .group_by(CoevoProjectMember.project_role)
                )).all()
                p["role_distribution"] = {role: cnt for role, cnt in role_rows if role}

        return projects
    except Exception as exc:
        logger.error("List coevo projects error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/projects/{project_id}")
async def get_coevo_project(project_id: int):
    """Full coevo project detail with members, claw_meetings, collabs, and research plans."""
    project = await Q.get_project_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        members = await Q.get_students_in_project(project_id)
        recent_meetings = await Q.get_recent_meetings(project_id=project_id, limit=10)
        collabs = await Q.get_collabs_in_project(project_id)
        research_plans = await Q.get_research_plans_in_project(project_id)

        # Openclaw insights for this project's claw_meetings
        async with get_db() as db:
            meeting_ids = [m["id"] for m in recent_meetings]
            insights = []
            if meeting_ids:
                rows = (await db.execute(
                    select(MeetingInsight)
                    .where(MeetingInsight.coevo_project_id == project_id)
                    .order_by(MeetingInsight.created_at.desc())
                    .limit(10)
                )).scalars().all()
                insights = [_insight_dict(i) for i in rows]

        return {
            "project": project,
            "members": members,
            "recent_meetings": recent_meetings,
            "claw_collaboration_recommendations": collabs,
            "research_plans": research_plans,
            "openclaw_insights": insights,
        }
    except Exception as exc:
        logger.error("Get coevo project error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Students ──────────────────────────────────────────────────────────────────

@router.get("/claw_students")
async def list_coevo_students(
    include_linked: bool = Query(True, description="Include openclaw link info"),
):
    """
    List all coevo claw_students enriched with openclaw link status and capability scores.
    """
    try:
        claw_students = await Q.get_all_students(role="student")

        if include_linked:
            async with get_db() as db:
                # Build link map: coevo_user_id → link info
                links = (await db.execute(select(CoevoStudentLink))).scalars().all()
                link_map = {l.coevo_user_id: l for l in links}

            for s in claw_students:
                link = link_map.get(s["id"])
                if link:
                    s["openclaw_student_id"] = link.openclaw_student_id
                    s["last_synced_at"] = str(link.last_synced_at) if link.last_synced_at else None
                else:
                    s["openclaw_student_id"] = None
                    s["last_synced_at"] = None

        return claw_students
    except Exception as exc:
        logger.error("List coevo claw_students error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/claw_students/{coevo_user_id}")
async def get_coevo_student(coevo_user_id: int):
    """
    Full coevo student profile:
    - Basic user info from coevo
    - All projects they're in
    - Meeting history with their pre/post reports
    - Collaboration recommendations
    - OpenClaw capability scores (if synced)
    - Meeting insights from openclaw
    """
    user = await Q.get_user_by_id(coevo_user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Student not found in coevo")

    try:
        meeting_history = await Q.get_student_meeting_history(coevo_user_id, limit=20)
        pre_reports = await Q.get_student_pre_reports(coevo_user_id, limit=10)
        post_reports = await Q.get_student_post_reports(coevo_user_id, limit=10)

        # Project memberships
        async with get_coevo_db() as coevo_db:
            memberships = (await coevo_db.execute(
                select(CoevoProjectMember, CoevoProject)
                .join(CoevoProject, CoevoProjectMember.project_id == CoevoProject.id)
                .where(CoevoProjectMember.user_id == coevo_user_id)
            )).all()
            projects = [
                {
                    "project_id": pm.project_id,
                    "project_name": proj.project_name,
                    "project_role": pm.project_role,
                    "project_auth": pm.project_auth,
                    "quarterly_goal": pm.quarterly_goal,
                    "short_term_goal": pm.short_term_goal,
                    "joined_at": str(pm.joined_at) if pm.joined_at else None,
                }
                for pm, proj in memberships
            ]

            # Collaboration recommendations where this student is requester
            collabs = (await coevo_db.execute(
                select(CoevoCollabRecommendation)
                .where(
                    CoevoCollabRecommendation.requester_user_id == coevo_user_id,
                    CoevoCollabRecommendation.status == "completed",
                )
                .order_by(CoevoCollabRecommendation.created_at.desc())
                .limit(5)
            )).scalars().all()
            collab_list = [Q._collab_dict(c) for c in collabs]

        # OpenClaw enrichment (if student is synced)
        openclaw_data = {}
        async with get_db() as db:
            link = (await db.execute(
                select(CoevoStudentLink).where(CoevoStudentLink.coevo_user_id == coevo_user_id)
            )).scalar_one_or_none()

            if link and link.openclaw_student_id:
                from gateway.routes.claw_students import _student_dict
                from models import CapabilityScore, CapabilityDimension, ProgressEvent
                student = (await db.execute(
                    select(Student).where(Student.id == link.openclaw_student_id)
                )).scalar_one_or_none()
                if student:
                    # Capability scores
                    scores = (await db.execute(
                        select(CapabilityDimension.name, CapabilityDimension.label, CapabilityScore.score, CapabilityScore.assessed_at)
                        .join(CapabilityDimension, CapabilityScore.dimension_id == CapabilityDimension.id)
                        .where(CapabilityScore.student_id == link.openclaw_student_id)
                        .order_by(CapabilityScore.assessed_at.desc())
                    )).all()
                    seen = set()
                    claw_capability_scores = []
                    for dim_name, label, score, assessed_at in scores:
                        if dim_name not in seen:
                            seen.add(dim_name)
                            claw_capability_scores.append({
                                "dimension": dim_name,
                                "label": label,
                                "score": float(score),
                                "assessed_at": str(assessed_at),
                            })

                    openclaw_data = {
                        "openclaw_student_id": link.openclaw_student_id,
                        "claw_capability_scores": claw_capability_scores,
                        "research_area": student.research_area,
                        "advisor_notes": student.advisor_notes,
                        "tags": student.tags,
                    }

            # Meeting insights for this student
            insights = (await db.execute(
                select(MeetingInsight)
                .where(MeetingInsight.affected_students.contains(f'[{coevo_user_id}'))
                .order_by(MeetingInsight.created_at.desc())
                .limit(5)
            )).scalars().all()
            insight_list = [_insight_dict(i) for i in insights]

        return {
            "user": user,
            "projects": projects,
            "meeting_history": meeting_history,
            "pre_reports": pre_reports,
            "post_reports": post_reports,
            "claw_collaboration_recommendations": collab_list,
            "openclaw_enrichment": openclaw_data,
            "claw_meeting_insights": insight_list,
        }
    except Exception as exc:
        logger.error("Get coevo student error for %d: %s", coevo_user_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Meetings ──────────────────────────────────────────────────────────────────

@router.get("/claw_meetings")
async def list_coevo_meetings(
    project_id: int | None = Query(None),
    limit: int = Query(30, ge=1, le=100),
):
    """List recent coevo claw_meetings with openclaw insight counts."""
    try:
        claw_meetings = await Q.get_recent_meetings(project_id=project_id, limit=limit)

        # Annotate with openclaw insight counts
        async with get_db() as db:
            for m in claw_meetings:
                insight_count = (await db.execute(
                    select(func.count(MeetingInsight.id))
                    .where(MeetingInsight.coevo_meeting_id == m["id"])
                )).scalar() or 0
                m["openclaw_insight_count"] = insight_count

        return claw_meetings
    except Exception as exc:
        logger.error("List coevo claw_meetings error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/claw_meetings/{meeting_id}")
async def get_coevo_meeting(meeting_id: int):
    """
    Full coevo meeting detail with all student reports and openclaw insights.
    """
    meeting = await Q.get_meeting_by_id(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    try:
        reports = await Q.get_meeting_reports(meeting_id)

        # Group reports by student and phase
        student_report_map: dict[int, dict] = {}
        for r in reports:
            uid = r["user_id"]
            if uid not in student_report_map:
                student_report_map[uid] = {
                    "user_id": uid,
                    "student_name": r["student_name"],
                    "student_email": r["student_email"],
                    "pre": None,
                    "post": None,
                }
            student_report_map[uid][r["phase"]] = r

        # OpenClaw insights for this meeting
        async with get_db() as db:
            insights = (await db.execute(
                select(MeetingInsight)
                .where(MeetingInsight.coevo_meeting_id == meeting_id)
                .order_by(MeetingInsight.created_at.desc())
            )).scalars().all()
            insight_list = [_insight_dict(i) for i in insights]

        return {
            "meeting": meeting,
            "student_reports": list(student_report_map.values()),
            "openclaw_insights": insight_list,
        }
    except Exception as exc:
        logger.error("Get coevo meeting error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Meeting Insights (OpenClaw-generated) ─────────────────────────────────────

@router.get("/insights")
async def list_insights(
    project_id: int | None = Query(None),
    insight_type: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    """List openclaw-generated meeting insights, optionally filtered."""
    async with get_db() as db:
        q = select(MeetingInsight)
        if project_id:
            q = q.where(MeetingInsight.coevo_project_id == project_id)
        if insight_type:
            q = q.where(MeetingInsight.insight_type == insight_type)
        q = q.order_by(MeetingInsight.created_at.desc()).limit(limit)
        rows = (await db.execute(q)).scalars().all()
        return [_insight_dict(i) for i in rows]


# ── Sync ──────────────────────────────────────────────────────────────────────

@router.post("/sync/claw_students")
async def trigger_student_sync(background_tasks: BackgroundTasks):
    """
    Trigger a background sync of coevo claw_students into openclaw_teamlab.
    Returns immediately with a job status; sync runs in the background.
    """
    async def _run_sync():
        try:
            result = await sync_students(triggered_by="api")
            logger.info("Background student sync done: %s", result)
        except Exception as exc:
            logger.error("Background student sync failed: %s", exc)

    background_tasks.add_task(_run_sync)
    return {"message": "Student sync started in background", "status": "queued"}


@router.post("/sync/claw_students/blocking")
async def trigger_student_sync_blocking():
    """Synchronous version — waits for sync to complete and returns results."""
    result = await sync_students(triggered_by="api")
    return result


@router.get("/sync/logs")
async def get_sync_logs(limit: int = Query(20, ge=1, le=100)):
    """Return recent sync operation logs."""
    async with get_db() as db:
        rows = (await db.execute(
            select(CoevoSyncLog)
            .order_by(CoevoSyncLog.started_at.desc())
            .limit(limit)
        )).scalars().all()
        return [
            {
                "id": l.id,
                "sync_type": l.sync_type,
                "triggered_by": l.triggered_by,
                "coevo_records_read": l.coevo_records_read,
                "openclaw_records_created": l.openclaw_records_created,
                "openclaw_records_updated": l.openclaw_records_updated,
                "status": l.status,
                "error_message": l.error_message,
                "started_at": str(l.started_at) if l.started_at else None,
                "completed_at": str(l.completed_at) if l.completed_at else None,
            }
            for l in rows
        ]


# ── Analytics ─────────────────────────────────────────────────────────────────

@router.get("/analytics/blockers")
async def student_blockers_analysis(
    project_id: int | None = Query(None),
    limit: int = Query(10, ge=1, le=50),
):
    """
    Aggregate key_blockers from recent pre-meeting reports across all claw_students.
    Useful for the PI to see systemic blockers across the team.
    """
    try:
        async with get_coevo_db() as db:
            q = (
                select(
                    CoevoUser.id.label("user_id"),
                    CoevoUser.username.label("student_name"),
                    CoevoMeetingReport.key_blockers,
                    CoevoMeetingReport.task_items,
                    CoevoMeeting.meeting_name,
                    CoevoMeeting.meeting_time,
                )
                .join(CoevoMeeting, CoevoMeetingReport.meeting_id == CoevoMeeting.id)
                .join(CoevoUser, CoevoMeetingReport.user_id == CoevoUser.id)
                .where(
                    CoevoMeetingReport.phase == "pre",
                    CoevoMeetingReport.status == "submitted",
                    CoevoMeetingReport.key_blockers.isnot(None),
                )
            )
            if project_id:
                q = q.where(CoevoMeeting.project_id == project_id)
            q = q.order_by(CoevoMeeting.meeting_time.desc()).limit(limit * 3)

            rows = (await db.execute(q)).all()

        # Group by student, keep most recent
        student_blockers: dict[int, dict] = {}
        for row in rows:
            if row.user_id not in student_blockers:
                student_blockers[row.user_id] = {
                    "user_id": row.user_id,
                    "student_name": row.student_name,
                    "recent_blockers": [],
                    "recent_tasks": [],
                }
            entry = student_blockers[row.user_id]
            if len(entry["recent_blockers"]) < 3 and row.key_blockers:
                entry["recent_blockers"].append({
                    "meeting_name": row.meeting_name,
                    "meeting_time": str(row.meeting_time) if row.meeting_time else None,
                    "blockers": row.key_blockers,
                })
            if len(entry["recent_tasks"]) < 2 and row.task_items:
                entry["recent_tasks"].append(row.task_items)

        return list(student_blockers.values())[:limit]

    except Exception as exc:
        logger.error("Student blockers analysis error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/analytics/meeting-engagement")
async def meeting_engagement_stats(project_id: int | None = Query(None)):
    """
    Calculate meeting engagement rate per student:
    - attendance rate
    - pre-report submission rate
    - how often teacher comments exist in post-reports
    """
    try:
        async with get_coevo_db() as db:
            # Get all claw_meetings for the scope
            meetings_q = select(CoevoMeeting).where(
                CoevoMeeting.status == "completed",
                CoevoMeeting.is_active == True,
            )
            if project_id:
                meetings_q = meetings_q.where(CoevoMeeting.project_id == project_id)
            claw_meetings = (await db.execute(meetings_q)).scalars().all()
            meeting_ids = [m.id for m in claw_meetings]

            if not meeting_ids:
                return []

            # Attendance per student
            from sqlalchemy import text
            # Count claw_meetings attended per user
            attendance_rows = (await db.execute(
                select(
                    CoevoUser.id,
                    CoevoUser.username,
                    func.count(CoevoMeetingReport.id).label("pre_count"),
                )
                .join(CoevoMeetingReport, CoevoMeetingReport.user_id == CoevoUser.id)
                .where(
                    CoevoMeetingReport.meeting_id.in_(meeting_ids),
                    CoevoMeetingReport.phase == "pre",
                    CoevoMeetingReport.status == "submitted",
                )
                .group_by(CoevoUser.id, CoevoUser.username)
                .order_by(func.count(CoevoMeetingReport.id).desc())
            )).all()

            total_meetings = len(meeting_ids)
            return [
                {
                    "user_id": row.id,
                    "student_name": row.username,
                    "pre_reports_submitted": row.pre_count,
                    "total_meetings": total_meetings,
                    "engagement_rate": round(row.pre_count / total_meetings, 2) if total_meetings else 0,
                }
                for row in attendance_rows
            ]

    except Exception as exc:
        logger.error("Meeting engagement stats error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/analytics/research-momentum")
async def research_momentum(project_id: int | None = Query(None)):
    """
    Analyze research momentum: completed collabs + research plan progress
    across coevo projects, enriched with openclaw capability data.
    """
    try:
        projects = await Q.get_all_projects()
        if project_id:
            projects = [p for p in projects if p["id"] == project_id]

        result = []
        for proj in projects:
            pid = proj["id"]
            collabs = await Q.get_collabs_in_project(pid)
            plans = await Q.get_research_plans_in_project(pid)
            members = await Q.get_students_in_project(pid)

            # Count members with openclaw capability data
            async with get_db() as db:
                from models import CapabilityScore
                synced_count = 0
                for m in members:
                    link = (await db.execute(
                        select(CoevoStudentLink)
                        .where(CoevoStudentLink.coevo_user_id == m["id"])
                    )).scalar_one_or_none()
                    if link and link.openclaw_student_id:
                        score_count = (await db.execute(
                            select(func.count(CapabilityScore.id))
                            .where(CapabilityScore.student_id == link.openclaw_student_id)
                        )).scalar() or 0
                        if score_count > 0:
                            synced_count += 1

            result.append({
                "project_id": pid,
                "project_name": proj["project_name"],
                "member_count": len(members),
                "completed_collaborations": len(collabs),
                "research_plans": len(plans),
                "openclaw_assessed_members": synced_count,
                "assessment_coverage": round(synced_count / len(members), 2) if members else 0,
            })

        return result

    except Exception as exc:
        logger.error("Research momentum error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Serializer ───────────────────────────────────────────────────────────────

def _insight_dict(i: MeetingInsight) -> dict:
    return {
        "id": i.id,
        "coevo_meeting_id": i.coevo_meeting_id,
        "coevo_project_id": i.coevo_project_id,
        "insight_type": i.insight_type,
        "title": i.title,
        "content": i.content,
        "signals": i.signals,
        "affected_students": i.affected_students,
        "confidence_score": float(i.confidence_score) if i.confidence_score else None,
        "generated_by": i.generated_by,
        "created_at": str(i.created_at) if i.created_at else None,
    }


# ── Risk Dashboard ──────────────────────────────────────────────────────────

@router.get("/risk/dashboard")
async def risk_dashboard():
    """Return latest risk scores for all claw_students, sorted by risk."""
    from sqlalchemy import text as sa_text
    try:
        async with get_db() as session:
            # Get latest score per student (subquery for max computed_at)
            result = await session.execute(sa_text("""
                SELECT s1.* FROM claw_student_risk_scores s1
                INNER JOIN (
                    SELECT coevo_user_id, MAX(computed_at) AS latest
                    FROM claw_student_risk_scores
                    GROUP BY coevo_user_id
                ) s2 ON s1.coevo_user_id = s2.coevo_user_id AND s1.computed_at = s2.latest
                ORDER BY s1.overall_score DESC
            """))
            rows = result.mappings().all()

        claw_students = []
        for r in rows:
            claw_students.append({
                "coevo_user_id": r["coevo_user_id"],
                "student_name": r["student_name"],
                "overall_score": float(r["overall_score"]),
                "blocker_persistence": float(r["blocker_persistence"]) if r["blocker_persistence"] else 0,
                "goal_completion_gap": float(r["goal_completion_gap"]) if r["goal_completion_gap"] else 0,
                "engagement_decline": float(r["engagement_decline"]) if r["engagement_decline"] else 0,
                "sentiment_score": float(r["sentiment_score"]) if r["sentiment_score"] else 0,
                "teacher_signal": float(r["teacher_signal"]) if r["teacher_signal"] else 0,
                "risk_level": r["risk_level"],
                "explanation": r["explanation"],
                "computed_at": str(r["computed_at"]) if r["computed_at"] else None,
            })

        summary = {
            "total": len(claw_students),
            "red": sum(1 for s in claw_students if s["risk_level"] == "red"),
            "yellow": sum(1 for s in claw_students if s["risk_level"] == "yellow"),
            "green": sum(1 for s in claw_students if s["risk_level"] == "green"),
        }

        return {"summary": summary, "claw_students": claw_students}

    except Exception as exc:
        logger.error("Risk dashboard error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/risk/{coevo_user_id}")
async def risk_detail(coevo_user_id: int):
    """Return full risk detail for a single student including signals breakdown."""
    from sqlalchemy import text as sa_text
    import json as _json
    try:
        async with get_db() as session:
            result = await session.execute(sa_text("""
                SELECT * FROM claw_student_risk_scores
                WHERE coevo_user_id = :uid
                ORDER BY computed_at DESC LIMIT 5
            """), {"uid": coevo_user_id})
            rows = result.mappings().all()

        if not rows:
            raise HTTPException(status_code=404, detail="No risk data for this student")

        latest = rows[0]
        signals_detail = latest["signals_detail"]
        if isinstance(signals_detail, str):
            signals_detail = _json.loads(signals_detail)

        history = [
            {"overall_score": float(r["overall_score"]), "risk_level": r["risk_level"],
             "computed_at": str(r["computed_at"])}
            for r in rows
        ]

        return {
            "coevo_user_id": coevo_user_id,
            "student_name": latest["student_name"],
            "overall_score": float(latest["overall_score"]),
            "risk_level": latest["risk_level"],
            "explanation": latest["explanation"],
            "signals": {
                "blocker_persistence": float(latest["blocker_persistence"]) if latest["blocker_persistence"] else 0,
                "goal_completion_gap": float(latest["goal_completion_gap"]) if latest["goal_completion_gap"] else 0,
                "engagement_decline": float(latest["engagement_decline"]) if latest["engagement_decline"] else 0,
                "sentiment_score": float(latest["sentiment_score"]) if latest["sentiment_score"] else 0,
                "teacher_signal": float(latest["teacher_signal"]) if latest["teacher_signal"] else 0,
            },
            "signals_detail": signals_detail,
            "history": history,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Risk detail error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/risk/compute")
async def trigger_risk_compute(background_tasks: BackgroundTasks):
    """Trigger risk score computation for all claw_students."""
    from data_bridge.risk_engine import compute_all_risks
    from data_bridge.risk_alerts import send_risk_alerts

    async def _run():
        results = await compute_all_risks()
        await send_risk_alerts(results)

    background_tasks.add_task(_run)
    return {"status": "computing", "message": "Risk computation started in background"}


# ── Growth Narrative ─────────────────────────────────────────────────────────

@router.get("/claw_students/{coevo_user_id}/narrative")
async def student_narrative(coevo_user_id: int, months: int = Query(3, ge=1, le=12)):
    """Get or generate a growth narrative for a student."""
    from sqlalchemy import text as sa_text
    import json as _json

    try:
        # Check cache first
        async with get_db() as session:
            result = await session.execute(sa_text("""
                SELECT * FROM claw_student_narratives
                WHERE coevo_user_id = :uid AND months_covered = :months
                ORDER BY generated_at DESC LIMIT 1
            """), {"uid": coevo_user_id, "months": months})
            cached = result.mappings().first()

        if cached:
            milestones = cached["key_milestones"]
            if isinstance(milestones, str):
                milestones = _json.loads(milestones)
            recommendations = cached["recommendations"]
            if isinstance(recommendations, str):
                recommendations = _json.loads(recommendations)
            return {
                "coevo_user_id": coevo_user_id,
                "student_name": cached["student_name"],
                "months_covered": cached["months_covered"],
                "narrative": cached["narrative_text"],
                "key_milestones": milestones,
                "current_assessment": cached["current_assessment"],
                "recommendations": recommendations,
                "generated_at": str(cached["generated_at"]),
                "cached": True,
            }

        # Generate fresh narrative
        from data_bridge.narrative import generate_student_narrative
        narrative = await generate_student_narrative(coevo_user_id, months)
        return {**narrative, "cached": False}

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Narrative error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Action Items ──────────────────────────────────────────────────────────────

@router.get("/actions")
async def list_actions(
    status: str = Query(None),
    user_id: int = Query(None),
    limit: int = Query(50, le=200),
):
    """List action items with optional status/user filter."""
    from sqlalchemy import text as sa_text
    try:
        conditions = ["1=1"]
        params: dict = {"lim": limit}

        if status:
            conditions.append("status = :status")
            params["status"] = status
        if user_id:
            conditions.append("coevo_user_id = :uid")
            params["uid"] = user_id

        where = " AND ".join(conditions)

        async with get_db() as session:
            result = await session.execute(sa_text(f"""
                SELECT * FROM claw_action_item_tracker
                WHERE {where}
                ORDER BY
                    CASE status
                        WHEN 'stale' THEN 0 WHEN 'open' THEN 1
                        WHEN 'in_progress' THEN 2 WHEN 'done' THEN 3
                    END,
                    created_at DESC
                LIMIT :lim
            """), params)
            rows = result.mappings().all()

        items = [dict(r) for r in rows]
        for item in items:
            for k in ("created_at", "updated_at", "stale_since"):
                if item.get(k):
                    item[k] = str(item[k])

        summary = {
            "total": len(items),
            "open": sum(1 for i in items if i.get("status") == "open"),
            "in_progress": sum(1 for i in items if i.get("status") == "in_progress"),
            "done": sum(1 for i in items if i.get("status") == "done"),
            "stale": sum(1 for i in items if i.get("status") == "stale"),
        }

        return {"summary": summary, "items": items}

    except Exception as exc:
        logger.error("Action items error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/actions/stale")
async def stale_actions():
    """List action items that are overdue (stale)."""
    from sqlalchemy import text as sa_text
    try:
        async with get_db() as session:
            result = await session.execute(sa_text("""
                SELECT * FROM claw_action_item_tracker
                WHERE status = 'stale'
                ORDER BY stale_since ASC
            """))
            rows = result.mappings().all()

        items = [dict(r) for r in rows]
        for item in items:
            for k in ("created_at", "updated_at", "stale_since"):
                if item.get(k):
                    item[k] = str(item[k])
        return {"count": len(items), "items": items}

    except Exception as exc:
        logger.error("Stale actions error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Innovation Index ─────────────────────────────────────────────────────────

@router.get("/innovation-index")
async def innovation_index():
    """
    Compute an innovation index (0-100) per CoEvo project based on 4 signals:
    - meeting_velocity: completed claw_meetings per member
    - collab_density: completed collaborations per member
    - blocker_resolution: pre-reports that include next-week plans (as %)
    - direction_breadth: coverage of openclaw research direction clusters
    Weighted composite → innovation_index.
    """
    try:
        projects = await Q.get_all_projects()
        result = []

        async with get_coevo_db() as coevo_db:
            for proj in projects:
                pid = proj["id"]

                # Meeting velocity: completed claw_meetings / member count
                completed_meetings = (await coevo_db.execute(
                    select(func.count(CoevoMeeting.id))
                    .where(
                        CoevoMeeting.project_id == pid,
                        CoevoMeeting.status == "completed",
                        CoevoMeeting.is_active == True,
                    )
                )).scalar() or 0

                member_count = (await coevo_db.execute(
                    select(func.count(CoevoProjectMember.id))
                    .where(CoevoProjectMember.project_id == pid)
                )).scalar() or 1  # avoid division by zero

                meeting_velocity_raw = completed_meetings / member_count
                meeting_velocity = min(meeting_velocity_raw * 10, 100)  # cap at 10 claw_meetings/person

                # Collab density: completed collabs / member count
                completed_collabs = (await coevo_db.execute(
                    select(func.count(CoevoCollabRecommendation.id))
                    .where(
                        CoevoCollabRecommendation.project_id == pid,
                        CoevoCollabRecommendation.status == "completed",
                    )
                )).scalar() or 0

                collab_density_raw = completed_collabs / member_count
                collab_density = min(collab_density_raw * 20, 100)  # cap at 5 collabs/person

                # Blocker resolution: pre-reports with next-week plan / total pre-reports
                total_pre = (await coevo_db.execute(
                    select(func.count(CoevoMeetingReport.id))
                    .join(CoevoMeeting, CoevoMeetingReport.meeting_id == CoevoMeeting.id)
                    .where(
                        CoevoMeeting.project_id == pid,
                        CoevoMeetingReport.phase == "pre",
                        CoevoMeetingReport.status == "submitted",
                        CoevoMeetingReport.key_blockers.isnot(None),
                    )
                )).scalar() or 0

                resolved_pre = (await coevo_db.execute(
                    select(func.count(CoevoMeetingReport.id))
                    .join(CoevoMeeting, CoevoMeetingReport.meeting_id == CoevoMeeting.id)
                    .where(
                        CoevoMeeting.project_id == pid,
                        CoevoMeetingReport.phase == "pre",
                        CoevoMeetingReport.status == "submitted",
                        CoevoMeetingReport.key_blockers.isnot(None),
                        CoevoMeetingReport.task_items.isnot(None),
                    )
                )).scalar() or 0

                blocker_resolution = (resolved_pre / total_pre * 100) if total_pre > 0 else 50.0

                # Direction breadth: claw_research_direction_clusters that reference this project
                direction_breadth = 0.0
                try:
                    from models import ResearchDirectionCluster
                    async with get_db() as ocdb:
                        cluster_count = (await ocdb.execute(
                            select(func.count(ResearchDirectionCluster.id))
                            .where(
                                ResearchDirectionCluster.is_active == True,
                                ResearchDirectionCluster.related_projects.like(f'%"id": {pid}%'),
                            )
                        )).scalar() or 0
                    direction_breadth = min(cluster_count * 20, 100)
                except Exception:
                    pass

                # Weighted composite (0-100)
                innovation_index_val = (
                    meeting_velocity * 0.25
                    + collab_density * 0.30
                    + blocker_resolution * 0.25
                    + direction_breadth * 0.20
                )

                result.append({
                    "project_id": pid,
                    "project_name": proj["project_name"],
                    "member_count": int(member_count),
                    "innovation_index": round(innovation_index_val, 1),
                    "components": {
                        "meeting_velocity": round(meeting_velocity, 1),
                        "collab_density": round(collab_density, 1),
                        "blocker_resolution": round(blocker_resolution, 1),
                        "direction_breadth": round(direction_breadth, 1),
                    },
                    "raw": {
                        "completed_meetings": int(completed_meetings),
                        "completed_collabs": int(completed_collabs),
                        "pre_reports_with_blockers": int(total_pre),
                        "pre_reports_resolved": int(resolved_pre),
                    },
                })

        # Sort by innovation index desc
        result.sort(key=lambda x: x["innovation_index"], reverse=True)
        return result

    except Exception as exc:
        logger.error("Innovation index error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Team Health ──────────────────────────────────────────────────────────────

@router.get("/team-health")
async def team_health():
    """Compute overall team health score from multiple signals."""
    from sqlalchemy import text as sa_text
    try:
        # 1. Average risk (inverted) — lower risk = higher health
        avg_risk = 50.0
        async with get_db() as session:
            result = await session.execute(sa_text("""
                SELECT AVG(s1.overall_score) AS avg_risk FROM claw_student_risk_scores s1
                INNER JOIN (
                    SELECT coevo_user_id, MAX(computed_at) AS latest
                    FROM claw_student_risk_scores GROUP BY coevo_user_id
                ) s2 ON s1.coevo_user_id = s2.coevo_user_id AND s1.computed_at = s2.latest
            """))
            row = result.mappings().first()
            if row and row["avg_risk"] is not None:
                avg_risk = float(row["avg_risk"])

        risk_health = max(0, 100 - avg_risk)

        # 2. Meeting engagement from CoEvo
        engagement_rate = 0.7
        try:
            async with get_coevo_db() as session:
                result = await session.execute(sa_text("""
                    SELECT
                        COUNT(DISTINCT ma.meeting_id) AS total,
                        COUNT(DISTINCT mr.id) AS reports
                    FROM meeting_attendees ma
                    LEFT JOIN meeting_reports mr
                        ON ma.meeting_id = mr.meeting_id AND ma.user_id = mr.user_id
                        AND mr.phase = 'pre'
                """))
                row = result.mappings().first()
                if row and row["total"] and row["total"] > 0:
                    engagement_rate = row["reports"] / row["total"]
        except Exception:
            pass

        engagement_health = engagement_rate * 100

        # 3. Blocker resolution rate
        blocker_rate = 0.5
        async with get_db() as session:
            result = await session.execute(sa_text("""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS resolved
                FROM claw_action_item_tracker
            """))
            row = result.mappings().first()
            if row and row["total"] and row["total"] > 0:
                blocker_rate = row["resolved"] / row["total"]

        blocker_health = blocker_rate * 100

        # 4. Collaboration activity
        collab_health = 50.0
        try:
            async with get_coevo_db() as session:
                result = await session.execute(sa_text("""
                    SELECT COUNT(*) AS cnt FROM claw_collaboration_recommendations
                    WHERE status = 'completed'
                """))
                row = result.mappings().first()
                collab_count = row["cnt"] if row else 0
                collab_health = min(collab_count * 10, 100)
        except Exception:
            pass

        # Weighted composite
        overall = (
            risk_health * 0.40
            + engagement_health * 0.25
            + blocker_health * 0.20
            + collab_health * 0.15
        )

        return {
            "overall_health": round(overall, 1),
            "components": {
                "risk_health": round(risk_health, 1),
                "engagement_health": round(engagement_health, 1),
                "blocker_resolution": round(blocker_health, 1),
                "collaboration_activity": round(collab_health, 1),
            },
            "details": {
                "avg_student_risk": round(avg_risk, 1),
                "engagement_rate": round(engagement_rate, 2),
                "blocker_resolution_rate": round(blocker_rate, 2),
            },
        }

    except Exception as exc:
        logger.error("Team health error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
