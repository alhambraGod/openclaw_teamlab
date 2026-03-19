"""
OpenClaw TeamLab — CoEvo Student Sync Service
Imports coevo users (claw_students) into openclaw_teamlab's claw_students + claw_coevo_student_links tables.
Runs on demand via /api/coevo/sync or scheduled by the scheduler.
"""
import logging
from datetime import datetime

from sqlalchemy import select

from config.coevo_db import get_coevo_db
from config.database import get_db
from models import Student, CoevoStudentLink, CoevoSyncLog
from models.coevo import CoevoUser, CoevoProjectMember, CoevoProject

logger = logging.getLogger("teamlab.data_bridge.sync")


async def sync_students(triggered_by: str = "api") -> dict:
    """
    Sync coevo claw_students into openclaw_teamlab.claw_students + claw_coevo_student_links.
    Returns a summary dict of what was created/updated.
    """
    sync_log_id: int | None = None

    # Create sync log entry
    async with get_db() as db:
        log = CoevoSyncLog(
            sync_type="claw_students",
            triggered_by=triggered_by,
            status="running",
        )
        db.add(log)
        await db.flush()
        sync_log_id = log.id

    created = updated = read_count = 0
    error_msg = None

    try:
        # Fetch all claw_students from coevo
        async with get_coevo_db() as coevo_db:
            students_q = (
                select(CoevoUser)
                .where(CoevoUser.role == "student", CoevoUser.is_active == True)
                .order_by(CoevoUser.id)
            )
            coevo_students = (await coevo_db.execute(students_q)).scalars().all()
            read_count = len(coevo_students)

            # For each student, also get their project memberships
            student_projects: dict[int, list[dict]] = {}
            for cs in coevo_students:
                memberships = (await coevo_db.execute(
                    select(CoevoProjectMember, CoevoProject)
                    .join(CoevoProject, CoevoProjectMember.project_id == CoevoProject.id)
                    .where(CoevoProjectMember.user_id == cs.id)
                )).all()
                student_projects[cs.id] = [
                    {
                        "project_id": pm.project_id,
                        "project_name": proj.project_name,
                        "project_role": pm.project_role,
                    }
                    for pm, proj in memberships
                ]

        # Now write into openclaw_teamlab DB
        async with get_db() as db:
            for cs in coevo_students:
                # Check if already linked
                existing_link = (await db.execute(
                    select(CoevoStudentLink).where(CoevoStudentLink.coevo_user_id == cs.id)
                )).scalar_one_or_none()

                projects_info = student_projects.get(cs.id, [])

                if existing_link:
                    # Update link metadata
                    existing_link.coevo_email = cs.email
                    existing_link.coevo_username = cs.username
                    existing_link.coevo_projects = projects_info
                    updated += 1

                    # Update openclaw student if linked
                    if existing_link.openclaw_student_id:
                        student = (await db.execute(
                            select(Student).where(Student.id == existing_link.openclaw_student_id)
                        )).scalar_one_or_none()
                        if student:
                            if cs.avatar_url and not student.avatar_url:
                                student.avatar_url = cs.avatar_url
                            if cs.bio and not student.bio:
                                student.bio = cs.bio
                else:
                    # Create openclaw Student record
                    new_student = Student(
                        name=cs.username or cs.email or f"User-{cs.id}",
                        email=cs.email,
                        avatar_url=cs.avatar_url,
                        bio=cs.bio,
                        research_area=_infer_research_area(projects_info),
                        status="active",
                        degree_type="phd",  # default; can be updated manually
                    )
                    db.add(new_student)
                    await db.flush()

                    # Create link
                    link = CoevoStudentLink(
                        openclaw_student_id=new_student.id,
                        coevo_user_id=cs.id,
                        coevo_email=cs.email,
                        coevo_username=cs.username,
                        coevo_role=cs.role,
                        coevo_projects=projects_info,
                    )
                    db.add(link)
                    created += 1

        logger.info(
            "Sync claw_students complete: read=%d created=%d updated=%d",
            read_count, created, updated,
        )

    except Exception as exc:
        error_msg = str(exc)
        logger.error("Sync claw_students failed: %s", exc, exc_info=True)

    # Update sync log
    async with get_db() as db:
        if sync_log_id:
            log = (await db.execute(
                select(CoevoSyncLog).where(CoevoSyncLog.id == sync_log_id)
            )).scalar_one_or_none()
            if log:
                log.coevo_records_read = read_count
                log.openclaw_records_created = created
                log.openclaw_records_updated = updated
                log.status = "failed" if error_msg else "completed"
                log.error_message = error_msg
                log.completed_at = datetime.utcnow()

    return {
        "sync_type": "claw_students",
        "triggered_by": triggered_by,
        "coevo_records_read": read_count,
        "openclaw_records_created": created,
        "openclaw_records_updated": updated,
        "status": "failed" if error_msg else "completed",
        "error_message": error_msg,
    }


def _infer_research_area(projects: list[dict]) -> str | None:
    """Derive a research area label from project memberships."""
    if not projects:
        return None
    names = [p.get("project_name", "") for p in projects if p.get("project_name")]
    return " / ".join(names[:3]) if names else None
