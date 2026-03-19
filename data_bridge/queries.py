"""
OpenClaw TeamLab — CoEvo Data Queries
Reusable async read queries against cognalign_coevo_prod.
All functions return plain dicts/lists — no SQLAlchemy objects leave this module.
"""
import logging
from typing import Any

from sqlalchemy import select, func, desc

from config.coevo_db import get_coevo_db
from models.coevo import (
    CoevoUser, CoevoProject, CoevoProjectMember,
    CoevoMeeting, CoevoMeetingReport, CoevoMeetingAttendee,
    CoevoCollabRecommendation, CoevoResearchPlan, CoevoAgentMemory,
)

logger = logging.getLogger("teamlab.data_bridge.queries")


# ── User / Student queries ────────────────────────────────────────────────────

async def get_all_students(role: str = "student") -> list[dict]:
    """Return all coevo users with the given role (default: student)."""
    async with get_coevo_db() as db:
        rows = (await db.execute(
            select(CoevoUser)
            .where(CoevoUser.role == role, CoevoUser.is_active == True)
            .order_by(CoevoUser.username)
        )).scalars().all()
        return [_user_dict(u) for u in rows]


async def get_user_by_id(user_id: int) -> dict | None:
    """Return a single coevo user by ID."""
    async with get_coevo_db() as db:
        u = (await db.execute(
            select(CoevoUser).where(CoevoUser.id == user_id)
        )).scalar_one_or_none()
        return _user_dict(u) if u else None


async def get_students_in_project(project_id: int) -> list[dict]:
    """Return all student members of a coevo project."""
    async with get_coevo_db() as db:
        rows = (await db.execute(
            select(CoevoUser, CoevoProjectMember)
            .join(CoevoProjectMember, CoevoProjectMember.user_id == CoevoUser.id)
            .where(
                CoevoProjectMember.project_id == project_id,
                CoevoUser.is_active == True,
            )
            .order_by(CoevoUser.username)
        )).all()
        result = []
        for user, member in rows:
            d = _user_dict(user)
            d["project_role"] = member.project_role
            d["project_auth"] = member.project_auth
            d["display_name"] = member.display_name
            d["quarterly_goal"] = member.quarterly_goal
            d["short_term_goal"] = member.short_term_goal
            d["joined_at"] = str(member.joined_at) if member.joined_at else None
            result.append(d)
        return result


# ── Project queries ───────────────────────────────────────────────────────────

async def get_all_projects() -> list[dict]:
    """Return all active coevo projects."""
    async with get_coevo_db() as db:
        rows = (await db.execute(
            select(CoevoProject)
            .where(CoevoProject.is_active == True)
            .order_by(CoevoProject.created_at.desc())
        )).scalars().all()
        return [_project_dict(p) for p in rows]


async def get_project_by_id(project_id: int) -> dict | None:
    async with get_coevo_db() as db:
        p = (await db.execute(
            select(CoevoProject).where(CoevoProject.id == project_id)
        )).scalar_one_or_none()
        return _project_dict(p) if p else None


async def get_project_stats() -> dict:
    """Aggregate stats across all coevo projects."""
    async with get_coevo_db() as db:
        total_projects = (await db.execute(
            select(func.count(CoevoProject.id)).where(CoevoProject.is_active == True)
        )).scalar() or 0

        total_members = (await db.execute(
            select(func.count(func.distinct(CoevoProjectMember.user_id)))
        )).scalar() or 0

        role_counts = (await db.execute(
            select(CoevoProjectMember.project_role, func.count(CoevoProjectMember.id))
            .group_by(CoevoProjectMember.project_role)
        )).all()

        total_meetings = (await db.execute(
            select(func.count(CoevoMeeting.id)).where(CoevoMeeting.is_active == True)
        )).scalar() or 0

        completed_meetings = (await db.execute(
            select(func.count(CoevoMeeting.id))
            .where(CoevoMeeting.status == "completed", CoevoMeeting.is_active == True)
        )).scalar() or 0

        return {
            "total_projects": total_projects,
            "total_members": total_members,
            "role_distribution": {role: cnt for role, cnt in role_counts if role},
            "total_meetings": total_meetings,
            "completed_meetings": completed_meetings,
        }


# ── Meeting queries ───────────────────────────────────────────────────────────

async def get_recent_meetings(project_id: int | None = None, limit: int = 20) -> list[dict]:
    """Return recent claw_meetings, optionally filtered by project."""
    async with get_coevo_db() as db:
        q = select(CoevoMeeting).where(CoevoMeeting.is_active == True)
        if project_id:
            q = q.where(CoevoMeeting.project_id == project_id)
        q = q.order_by(CoevoMeeting.meeting_time.desc()).limit(limit)
        rows = (await db.execute(q)).scalars().all()
        return [_meeting_dict(m) for m in rows]


async def get_meeting_by_id(meeting_id: int) -> dict | None:
    async with get_coevo_db() as db:
        m = (await db.execute(
            select(CoevoMeeting).where(CoevoMeeting.id == meeting_id)
        )).scalar_one_or_none()
        return _meeting_dict(m) if m else None


async def get_meeting_reports(meeting_id: int) -> list[dict]:
    """Return all reports for a meeting (pre + post, all claw_students)."""
    async with get_coevo_db() as db:
        rows = (await db.execute(
            select(CoevoMeetingReport, CoevoUser)
            .join(CoevoUser, CoevoMeetingReport.user_id == CoevoUser.id)
            .where(CoevoMeetingReport.meeting_id == meeting_id)
            .order_by(CoevoMeetingReport.phase, CoevoUser.username)
        )).all()
        result = []
        for report, user in rows:
            d = _report_dict(report)
            d["student_name"] = user.username
            d["student_email"] = user.email
            result.append(d)
        return result


async def get_student_meeting_history(coevo_user_id: int, limit: int = 30) -> list[dict]:
    """Return a student's meeting participation history with their reports."""
    async with get_coevo_db() as db:
        # claw_meetings the student attended
        rows = (await db.execute(
            select(CoevoMeeting, CoevoMeetingAttendee)
            .join(CoevoMeetingAttendee, CoevoMeetingAttendee.meeting_id == CoevoMeeting.id)
            .where(
                CoevoMeetingAttendee.user_id == coevo_user_id,
                CoevoMeeting.is_active == True,
            )
            .order_by(CoevoMeeting.meeting_time.desc())
            .limit(limit)
        )).all()

        result = []
        for meeting, attendance in rows:
            entry = _meeting_dict(meeting)
            entry["attended"] = attendance.attended
            # Fetch this student's reports
            reports = (await db.execute(
                select(CoevoMeetingReport)
                .where(
                    CoevoMeetingReport.meeting_id == meeting.id,
                    CoevoMeetingReport.user_id == coevo_user_id,
                )
            )).scalars().all()
            entry["my_reports"] = [_report_dict(r) for r in reports]
            result.append(entry)
        return result


# ── Collaboration & Research queries ─────────────────────────────────────────

async def get_collabs_in_project(project_id: int) -> list[dict]:
    """Return completed collaboration recommendations for a project."""
    async with get_coevo_db() as db:
        rows = (await db.execute(
            select(CoevoCollabRecommendation, CoevoUser)
            .join(CoevoUser, CoevoCollabRecommendation.requester_user_id == CoevoUser.id)
            .where(
                CoevoCollabRecommendation.project_id == project_id,
                CoevoCollabRecommendation.status == "completed",
            )
            .order_by(CoevoCollabRecommendation.created_at.desc())
        )).all()
        result = []
        for rec, user in rows:
            d = _collab_dict(rec)
            d["requester_name"] = user.username
            d["requester_email"] = user.email
            result.append(d)
        return result


async def get_research_plans_in_project(project_id: int) -> list[dict]:
    """Return completed research plans for a project."""
    async with get_coevo_db() as db:
        rows = (await db.execute(
            select(CoevoResearchPlan)
            .where(
                CoevoResearchPlan.project_id == project_id,
                CoevoResearchPlan.status == "completed",
            )
            .order_by(CoevoResearchPlan.created_at.desc())
        )).scalars().all()
        return [_research_plan_dict(r) for r in rows]


async def get_agent_memories(project_id: int, user_id: int | None = None, limit: int = 20) -> list[dict]:
    """Return recent CAMA memories for a project (optionally filtered by user)."""
    async with get_coevo_db() as db:
        q = select(CoevoAgentMemory).where(CoevoAgentMemory.project_id == project_id)
        if user_id:
            q = q.where(CoevoAgentMemory.user_id == user_id)
        q = q.order_by(CoevoAgentMemory.created_at.desc()).limit(limit)
        rows = (await db.execute(q)).scalars().all()
        return [_memory_dict(m) for m in rows]


# ── Student blocker / progress aggregation ───────────────────────────────────

async def get_student_pre_reports(coevo_user_id: int, limit: int = 10) -> list[dict]:
    """Return a student's pre-meeting reports, ordered newest first."""
    async with get_coevo_db() as db:
        rows = (await db.execute(
            select(CoevoMeetingReport, CoevoMeeting)
            .join(CoevoMeeting, CoevoMeetingReport.meeting_id == CoevoMeeting.id)
            .where(
                CoevoMeetingReport.user_id == coevo_user_id,
                CoevoMeetingReport.phase == "pre",
                CoevoMeetingReport.status == "submitted",
            )
            .order_by(CoevoMeeting.meeting_time.desc())
            .limit(limit)
        )).all()
        result = []
        for report, meeting in rows:
            d = _report_dict(report)
            d["meeting_name"] = meeting.meeting_name
            d["meeting_time"] = str(meeting.meeting_time) if meeting.meeting_time else None
            result.append(d)
        return result


async def get_student_post_reports(coevo_user_id: int, limit: int = 10) -> list[dict]:
    """Return a student's post-meeting AI-generated reports, newest first."""
    async with get_coevo_db() as db:
        rows = (await db.execute(
            select(CoevoMeetingReport, CoevoMeeting)
            .join(CoevoMeeting, CoevoMeetingReport.meeting_id == CoevoMeeting.id)
            .where(
                CoevoMeetingReport.user_id == coevo_user_id,
                CoevoMeetingReport.phase == "post",
            )
            .order_by(CoevoMeeting.meeting_time.desc())
            .limit(limit)
        )).all()
        result = []
        for report, meeting in rows:
            d = _report_dict(report)
            d["meeting_name"] = meeting.meeting_name
            d["meeting_time"] = str(meeting.meeting_time) if meeting.meeting_time else None
            result.append(d)
        return result


# ── Serializers ──────────────────────────────────────────────────────────────

def _user_dict(u: CoevoUser) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "username": u.username,
        "role": u.role,
        "avatar_url": u.avatar_url,
        "bio": u.bio,
        "is_active": u.is_active,
        "last_login_at": str(u.last_login_at) if u.last_login_at else None,
        "created_at": str(u.created_at) if u.created_at else None,
    }


def _project_dict(p: CoevoProject) -> dict:
    return {
        "id": p.id,
        "project_name": p.project_name,
        "project_code": p.project_code,
        "description": p.description,
        "creator_user_id": p.creator_user_id,
        "member_count": p.member_count,
        "is_active": p.is_active,
        "settings": p.settings,
        "created_at": str(p.created_at) if p.created_at else None,
        "updated_at": str(p.updated_at) if p.updated_at else None,
    }


def _meeting_dict(m: CoevoMeeting) -> dict:
    return {
        "id": m.id,
        "project_id": m.project_id,
        "meeting_name": m.meeting_name,
        "meeting_time": str(m.meeting_time) if m.meeting_time else None,
        "phase": m.phase,
        "status": m.status,
        "overall_summary": m.overall_summary,
        "is_active": m.is_active,
        "created_at": str(m.created_at) if m.created_at else None,
    }


def _report_dict(r: CoevoMeetingReport) -> dict:
    return {
        "id": r.id,
        "meeting_id": r.meeting_id,
        "user_id": r.user_id,
        "phase": r.phase,
        "task_items": r.task_items,
        "key_blockers": r.key_blockers,
        "next_week_plan": r.next_week_plan,
        "remarks": r.remarks,
        "dialogue_detail": r.dialogue_detail,
        "core_viewpoints": r.core_viewpoints,
        "issues_recorded": r.issues_recorded,
        "teacher_suggestions": r.teacher_suggestions,
        "teacher_comments": r.teacher_comments,
        "student_summary": r.student_summary,
        "pm_notes": r.pm_notes,
        "status": r.status,
        "submitted_at": str(r.submitted_at) if r.submitted_at else None,
    }


def _collab_dict(c: CoevoCollabRecommendation) -> dict:
    return {
        "id": c.id,
        "project_id": c.project_id,
        "requester_user_id": c.requester_user_id,
        "target_user_ids": c.target_user_ids,
        "mode": c.mode,
        "collaboration_direction": c.collaboration_direction,
        "collaboration_suggestion": c.collaboration_suggestion,
        "expected_output": c.expected_output,
        "best_partner_analysis": c.best_partner_analysis,
        "status": c.status,
        "created_at": str(c.created_at) if c.created_at else None,
    }


def _research_plan_dict(r: CoevoResearchPlan) -> dict:
    return {
        "id": r.id,
        "project_id": r.project_id,
        "creator_user_id": r.creator_user_id,
        "plan_name": r.plan_name,
        "total_cycles": r.total_cycles,
        "nodes": r.nodes,
        "final_goal": r.final_goal,
        "final_expected_effect": r.final_expected_effect,
        "status": r.status,
        "created_at": str(r.created_at) if r.created_at else None,
    }


def _memory_dict(m: CoevoAgentMemory) -> dict:
    return {
        "id": m.id,
        "project_id": m.project_id,
        "user_id": m.user_id,
        "meeting_id": m.meeting_id,
        "memory_type": m.memory_type,
        "content": m.content,
        "relevance_score": m.relevance_score,
        "reference_count": m.reference_count,
        "cycle_id": m.cycle_id,
        "created_at": str(m.created_at) if m.created_at else None,
    }


# ── Cross-project aggregation ─────────────────────────────────────────────────

async def get_all_collabs(limit: int = 100) -> list[dict]:
    """Return all completed collaboration recommendations, enriched with user names."""
    async with get_coevo_db() as db:
        rows = (await db.execute(
            select(CoevoCollabRecommendation, CoevoUser, CoevoProject)
            .join(CoevoUser, CoevoCollabRecommendation.requester_user_id == CoevoUser.id)
            .join(CoevoProject, CoevoCollabRecommendation.project_id == CoevoProject.id)
            .where(CoevoCollabRecommendation.status == "completed")
            .order_by(CoevoCollabRecommendation.created_at.desc())
            .limit(limit)
        )).all()

        # Build user-id → name map for target_user_ids resolution
        all_user_ids = set()
        raw_recs = []
        for rec, requester, project in rows:
            d = _collab_dict(rec)
            d["requester_name"] = requester.username
            d["project_name"] = project.project_name
            d["student_a"] = requester.username
            # target_user_ids may be a list like [123, 456]
            targets = rec.target_user_ids or []
            if isinstance(targets, int):
                targets = [targets]
            d["_target_ids"] = targets
            all_user_ids.update(targets)
            raw_recs.append(d)

        # Resolve target user names in bulk
        user_name_map: dict[int, str] = {}
        if all_user_ids:
            users = (await db.execute(
                select(CoevoUser.id, CoevoUser.username)
                .where(CoevoUser.id.in_(list(all_user_ids)))
            )).all()
            user_name_map = {uid: name for uid, name in users}

        result = []
        for d in raw_recs:
            targets = d.pop("_target_ids", [])
            target_names = [user_name_map.get(uid, str(uid)) for uid in targets]
            d["target_names"] = target_names
            d["student_b"] = target_names[0] if target_names else ""
            # Map to openclaw collab shape
            d["complementarity_score"] = 0.8  # CoEvo doesn't have this; default high
            d["research_idea"] = d.get("collaboration_direction") or ""
            d["rationale"] = d.get("collaboration_suggestion") or ""
            result.append(d)

        return result


async def get_all_research_plans(limit: int = 50) -> list[dict]:
    """Return all completed research plans across all projects, with project name."""
    async with get_coevo_db() as db:
        rows = (await db.execute(
            select(CoevoResearchPlan, CoevoProject, CoevoUser)
            .join(CoevoProject, CoevoResearchPlan.project_id == CoevoProject.id)
            .join(CoevoUser, CoevoResearchPlan.creator_user_id == CoevoUser.id)
            .where(CoevoResearchPlan.status == "completed")
            .order_by(CoevoResearchPlan.created_at.desc())
            .limit(limit)
        )).all()
        result = []
        for plan, project, creator in rows:
            d = _research_plan_dict(plan)
            d["project_name"] = project.project_name
            d["creator_name"] = creator.username
            # Map to openclaw direction shape for the UI
            d["title"] = plan.plan_name or "研究规划"
            d["description"] = plan.final_goal or ""
            d["source"] = "meeting_derived"
            d["status"] = "active"
            d["priority"] = 5
            d["parent_id"] = None
            d["evidence"] = plan.final_expected_effect or ""
            d["related_student_names"] = [creator.username]
            result.append(d)
        return result


async def get_members_with_goals(project_id: int | None = None) -> list[dict]:
    """Return claw_students with their project membership details (goals, display_name, role)."""
    async with get_coevo_db() as db:
        q = (
            select(CoevoUser, CoevoProjectMember, CoevoProject)
            .join(CoevoProjectMember, CoevoProjectMember.user_id == CoevoUser.id)
            .join(CoevoProject, CoevoProjectMember.project_id == CoevoProject.id)
            .where(
                CoevoUser.is_active == True,
                CoevoProject.is_active == True,
            )
        )
        if project_id:
            q = q.where(CoevoProjectMember.project_id == project_id)
        q = q.order_by(CoevoProject.id, CoevoProjectMember.project_role, CoevoUser.username)
        rows = (await db.execute(q)).all()

        result = []
        seen_user_ids = set()
        for user, member, project in rows:
            uid = user.id
            if uid in seen_user_ids:
                continue
            seen_user_ids.add(uid)
            d = _user_dict(user)
            d["display_name"] = member.display_name or user.username
            d["project_role"] = member.project_role
            d["project_name"] = project.project_name
            d["project_id"] = project.id
            d["quarterly_goal"] = member.quarterly_goal
            d["short_term_goal"] = member.short_term_goal
            # Map to openclaw student shape
            d["name"] = member.display_name or user.username
            d["research_area"] = user.bio or ""
            d["degree_type"] = _map_rank(member.project_role)
            d["status"] = "active" if user.is_active else "inactive"
            result.append(d)
        return result


def _map_rank(project_role: str | None) -> str:
    """Map coevo project_role to openclaw degree_type."""
    mapping = {
        "teacher": "postdoc",
        "researcher": "phd",
        "pm": "phd",
        "student": "master",
    }
    return mapping.get(project_role or "", "phd")

