"""
OpenClaw TeamLab — Meeting Routes
List, create, and view meetings with optional AI summary triggering.
"""
import json
import logging
from datetime import datetime
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from config.database import get_db, get_redis, rkey
from models import Meeting

logger = logging.getLogger("teamlab.routes.meetings")
router = APIRouter(prefix="/meetings", tags=["meetings"])


# ── Pydantic schemas ──

class MeetingCreate(BaseModel):
    title: Optional[str] = None
    meeting_type: str
    meeting_date: datetime
    duration_min: Optional[int] = None
    attendees: Optional[list] = None
    raw_notes: Optional[str] = None
    summary: Optional[str] = None
    topics: Optional[list] = None
    action_items: Optional[list] = None


def _meeting_dict(m: Meeting) -> dict:
    return {
        "id": m.id,
        "title": m.title,
        "meeting_type": m.meeting_type,
        "meeting_date": str(m.meeting_date) if m.meeting_date else None,
        "duration_min": m.duration_min,
        "attendees": m.attendees,
        "raw_notes": m.raw_notes,
        "summary": m.summary,
        "topics": m.topics,
        "action_items": m.action_items,
        "created_at": str(m.created_at) if m.created_at else None,
    }


# ── Routes ──

@router.get("")
async def list_meetings():
    """List all meetings ordered by date descending."""
    async with get_db() as db:
        result = (await db.execute(
            select(Meeting).order_by(Meeting.meeting_date.desc())
        )).scalars().all()
        return [_meeting_dict(m) for m in result]


@router.post("")
async def create_meeting(body: MeetingCreate):
    """Create a meeting. If raw_notes provided and no summary, triggers AI summary task."""
    async with get_db() as db:
        meeting = Meeting(**body.model_dump(exclude_none=True))
        db.add(meeting)
        await db.flush()
        await db.refresh(meeting)

        # Trigger AI summary if raw notes provided but no summary
        if body.raw_notes and not body.summary:
            try:
                task_id = uuid4().hex[:16]
                r = await get_redis()
                payload = json.dumps({
                    "task_id": task_id,
                    "user_id": "system",
                    "source": "api",
                    "skill": "meeting_summarize",
                    "input_text": body.raw_notes,
                    "created_at": datetime.utcnow().isoformat(),
                    "context": {"meeting_id": meeting.id},
                }, ensure_ascii=False)
                await r.lpush(rkey("task_queue"), payload)
                logger.info("Queued meeting summary task %s for meeting %d", task_id, meeting.id)
            except Exception as exc:
                logger.warning("Failed to queue summary task: %s", exc)

        return _meeting_dict(meeting)


@router.get("/{meeting_id}")
async def get_meeting(meeting_id: int):
    """Single meeting detail."""
    async with get_db() as db:
        meeting = (await db.execute(
            select(Meeting).where(Meeting.id == meeting_id)
        )).scalar_one_or_none()
        if not meeting:
            raise HTTPException(status_code=404, detail="Meeting not found")
        return _meeting_dict(meeting)
