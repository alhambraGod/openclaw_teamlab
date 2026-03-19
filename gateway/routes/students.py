"""
OpenClaw TeamLab — Student Routes
CRUD operations and capability radar / timeline views.
"""
import logging
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from config.database import get_db
from models import Student, CapabilityScore, CapabilityDimension, ProgressEvent

logger = logging.getLogger("teamlab.routes.claw_students")
router = APIRouter(prefix="/claw_students", tags=["claw_students"])


# ── Pydantic schemas ──

class StudentCreate(BaseModel):
    name: str
    email: Optional[str] = None
    feishu_open_id: Optional[str] = None
    avatar_url: Optional[str] = None
    research_area: Optional[str] = None
    bio: Optional[str] = None
    enrollment_date: Optional[date] = None
    degree_type: Optional[str] = "phd"
    advisor_notes: Optional[str] = None
    tags: Optional[list] = None
    status: Optional[str] = "active"


class StudentUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    feishu_open_id: Optional[str] = None
    avatar_url: Optional[str] = None
    research_area: Optional[str] = None
    bio: Optional[str] = None
    enrollment_date: Optional[date] = None
    degree_type: Optional[str] = None
    advisor_notes: Optional[str] = None
    tags: Optional[list] = None
    status: Optional[str] = None


def _student_dict(s: Student, latest_scores: list | None = None) -> dict:
    d = {
        "id": s.id,
        "name": s.name,
        "email": s.email,
        "feishu_open_id": s.feishu_open_id,
        "avatar_url": s.avatar_url,
        "research_area": s.research_area,
        "bio": s.bio,
        "enrollment_date": str(s.enrollment_date) if s.enrollment_date else None,
        "degree_type": s.degree_type,
        "advisor_notes": s.advisor_notes,
        "tags": s.tags,
        "status": s.status,
        "created_at": str(s.created_at) if s.created_at else None,
        "updated_at": str(s.updated_at) if s.updated_at else None,
    }
    if latest_scores is not None:
        d["latest_scores"] = latest_scores
    return d


# ── Routes ──

@router.get("")
async def list_students():
    """List all claw_students with their latest capability scores."""
    async with get_db() as db:
        claw_students = (await db.execute(
            select(Student).order_by(Student.name)
        )).scalars().all()

        result = []
        for s in claw_students:
            # Latest score per dimension via correlated subquery
            latest_q = (
                select(
                    CapabilityDimension.name.label("dimension"),
                    CapabilityDimension.label,
                    CapabilityScore.score,
                    CapabilityScore.assessed_at,
                )
                .join(CapabilityDimension, CapabilityScore.dimension_id == CapabilityDimension.id)
                .where(CapabilityScore.student_id == s.id)
                .order_by(CapabilityScore.assessed_at.desc())
            )
            rows = (await db.execute(latest_q)).all()

            # Keep only the latest per dimension
            seen = set()
            scores = []
            for dim_name, label, score, assessed_at in rows:
                if dim_name not in seen:
                    seen.add(dim_name)
                    scores.append({
                        "dimension": dim_name,
                        "label": label,
                        "score": float(score),
                        "assessed_at": str(assessed_at),
                    })

            result.append(_student_dict(s, latest_scores=scores))

        return result


@router.get("/{student_id}")
async def get_student(student_id: int):
    """Full student profile with scores history and events."""
    async with get_db() as db:
        student = (await db.execute(
            select(Student).where(Student.id == student_id)
        )).scalar_one_or_none()
        if not student:
            raise HTTPException(status_code=404, detail="Student not found")

        # All scores with dimension info
        scores_q = (
            select(
                CapabilityDimension.name.label("dimension"),
                CapabilityDimension.label,
                CapabilityScore.score,
                CapabilityScore.assessed_at,
                CapabilityScore.assessed_by,
                CapabilityScore.evidence,
            )
            .join(CapabilityDimension, CapabilityScore.dimension_id == CapabilityDimension.id)
            .where(CapabilityScore.student_id == student_id)
            .order_by(CapabilityScore.assessed_at.desc())
        )
        scores = [
            {
                "dimension": r.dimension,
                "label": r.label,
                "score": float(r.score),
                "assessed_at": str(r.assessed_at),
                "assessed_by": r.assessed_by,
                "evidence": r.evidence,
            }
            for r in (await db.execute(scores_q)).all()
        ]

        # Events
        events_q = (
            select(ProgressEvent)
            .where(ProgressEvent.student_id == student_id)
            .order_by(ProgressEvent.event_date.desc())
        )
        events = [
            {
                "id": e.id,
                "event_type": e.event_type,
                "title": e.title,
                "description": e.description,
                "metadata": e.metadata,
                "event_date": str(e.event_date),
            }
            for e in (await db.execute(events_q)).scalars().all()
        ]

        data = _student_dict(student)
        data["scores_history"] = scores
        data["events"] = events
        return data


@router.post("")
async def create_student(body: StudentCreate):
    """Create a new student."""
    async with get_db() as db:
        student = Student(**body.model_dump(exclude_none=True))
        db.add(student)
        await db.flush()
        await db.refresh(student)
        return _student_dict(student)


@router.put("/{student_id}")
async def update_student(student_id: int, body: StudentUpdate):
    """Update an existing student."""
    async with get_db() as db:
        student = (await db.execute(
            select(Student).where(Student.id == student_id)
        )).scalar_one_or_none()
        if not student:
            raise HTTPException(status_code=404, detail="Student not found")

        for field, value in body.model_dump(exclude_none=True).items():
            setattr(student, field, value)
        await db.flush()
        await db.refresh(student)
        return _student_dict(student)


@router.get("/{student_id}/radar")
async def student_radar(student_id: int, compare_date: Optional[date] = Query(None)):
    """Radar chart data: all dimensions with scores, optional historical overlay."""
    async with get_db() as db:
        # Verify student exists
        student = (await db.execute(
            select(Student).where(Student.id == student_id)
        )).scalar_one_or_none()
        if not student:
            raise HTTPException(status_code=404, detail="Student not found")

        # All dimensions
        dims = (await db.execute(
            select(CapabilityDimension).order_by(CapabilityDimension.sort_order)
        )).scalars().all()

        # Latest scores
        all_scores_q = (
            select(CapabilityScore)
            .where(CapabilityScore.student_id == student_id)
            .order_by(CapabilityScore.assessed_at.desc())
        )
        all_scores = (await db.execute(all_scores_q)).scalars().all()

        # Latest per dimension
        latest_map: dict[int, float] = {}
        for sc in all_scores:
            if sc.dimension_id not in latest_map:
                latest_map[sc.dimension_id] = float(sc.score)

        # Historical overlay
        compare_map: dict[int, float] = {}
        if compare_date:
            hist_q = (
                select(CapabilityScore)
                .where(
                    CapabilityScore.student_id == student_id,
                    CapabilityScore.assessed_at <= compare_date,
                )
                .order_by(CapabilityScore.assessed_at.desc())
            )
            hist_scores = (await db.execute(hist_q)).scalars().all()
            for sc in hist_scores:
                if sc.dimension_id not in compare_map:
                    compare_map[sc.dimension_id] = float(sc.score)

        axes = []
        for d in dims:
            entry = {
                "dimension": d.name,
                "label": d.label,
                "category": d.category,
                "current": latest_map.get(d.id, 0.0),
            }
            if compare_date:
                entry["compare"] = compare_map.get(d.id, 0.0)
                entry["compare_date"] = str(compare_date)
            axes.append(entry)

        return {"student_id": student_id, "student_name": student.name, "axes": axes}


@router.get("/{student_id}/timeline")
async def student_timeline(student_id: int):
    """Timeline events for a student."""
    async with get_db() as db:
        student = (await db.execute(
            select(Student).where(Student.id == student_id)
        )).scalar_one_or_none()
        if not student:
            raise HTTPException(status_code=404, detail="Student not found")

        events_q = (
            select(ProgressEvent)
            .where(ProgressEvent.student_id == student_id)
            .order_by(ProgressEvent.event_date.desc())
        )
        events = (await db.execute(events_q)).scalars().all()
        return [
            {
                "id": e.id,
                "event_type": e.event_type,
                "title": e.title,
                "description": e.description,
                "metadata": e.metadata,
                "event_date": str(e.event_date),
                "created_at": str(e.created_at) if e.created_at else None,
            }
            for e in events
        ]
