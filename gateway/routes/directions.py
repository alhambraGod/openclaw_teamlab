"""
OpenClaw TeamLab — Research Direction Routes
CRUD for research directions with tree structure support.
"""
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func

from config.database import get_db
from models import ResearchDirection

logger = logging.getLogger("teamlab.routes.directions")
router = APIRouter(prefix="/directions", tags=["directions"])


# ── Pydantic schemas ──

class DirectionCreate(BaseModel):
    title: str
    description: Optional[str] = None
    source: str
    status: Optional[str] = "exploring"
    related_students: Optional[list] = None
    related_meetings: Optional[list] = None
    evidence: Optional[str] = None
    priority: Optional[int] = 5
    parent_id: Optional[int] = None


class DirectionUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    source: Optional[str] = None
    status: Optional[str] = None
    related_students: Optional[list] = None
    related_meetings: Optional[list] = None
    evidence: Optional[str] = None
    priority: Optional[int] = None
    parent_id: Optional[int] = None


def _direction_dict(d: ResearchDirection, children: list | None = None) -> dict:
    result = {
        "id": d.id,
        "title": d.title,
        "description": d.description,
        "source": d.source,
        "status": d.status,
        "related_students": d.related_students,
        "related_meetings": d.related_meetings,
        "evidence": d.evidence,
        "priority": d.priority,
        "parent_id": d.parent_id,
        "created_at": str(d.created_at) if d.created_at else None,
        "updated_at": str(d.updated_at) if d.updated_at else None,
    }
    if children is not None:
        result["children"] = children
    return result


def _build_tree(directions: list[ResearchDirection]) -> list[dict]:
    """Build a tree structure from flat list using parent_id."""
    by_id: dict[int, dict] = {}
    roots: list[dict] = []

    # First pass: create all nodes
    for d in directions:
        by_id[d.id] = _direction_dict(d, children=[])

    # Second pass: link children
    for d in directions:
        node = by_id[d.id]
        if d.parent_id and d.parent_id in by_id:
            by_id[d.parent_id]["children"].append(node)
        else:
            roots.append(node)

    return roots


def _cluster_dict(c) -> dict:
    return {
        "id": c.id,
        "topic": c.topic,
        "description": c.description,
        "keywords": c.keywords or [],
        "similarity_group": c.similarity_group,
        "related_projects": c.related_projects or [],
        "related_students": c.related_students or [],
        "confidence": float(c.confidence) if c.confidence else 0,
        "source_evidence": c.source_evidence,
        "generated_at": str(c.generated_at) if c.generated_at else None,
        "is_active": c.is_active,
    }


def _idea_dict(i) -> dict:
    return {
        "id": i.id,
        "title": i.title,
        "description": i.description,
        "inspiration_source": i.inspiration_source,
        "related_cluster_id": i.related_cluster_id,
        "international_refs": i.international_refs or [],
        "status": i.status,
        "created_at": str(i.created_at) if i.created_at else None,
    }


# ── Routes ──

@router.get("")
async def list_directions():
    """List all research directions as a tree structure."""
    async with get_db() as db:
        result = (await db.execute(
            select(ResearchDirection).order_by(ResearchDirection.priority.desc())
        )).scalars().all()
        return _build_tree(result)


@router.post("")
async def create_direction(body: DirectionCreate):
    """Create a new research direction."""
    async with get_db() as db:
        direction = ResearchDirection(**body.model_dump(exclude_none=True))
        db.add(direction)
        await db.flush()
        await db.refresh(direction)
        return _direction_dict(direction)


@router.put("/{direction_id}")
async def update_direction(direction_id: int, body: DirectionUpdate):
    """Update a research direction."""
    async with get_db() as db:
        direction = (await db.execute(
            select(ResearchDirection).where(ResearchDirection.id == direction_id)
        )).scalar_one_or_none()
        if not direction:
            raise HTTPException(status_code=404, detail="Direction not found")

        for field, value in body.model_dump(exclude_none=True).items():
            setattr(direction, field, value)
        await db.flush()
        await db.refresh(direction)
        return _direction_dict(direction)


# ── Research Direction Clusters (AI-analyzed) ────────────────────────────────

@router.get("/clusters")
async def list_direction_clusters():
    """AI-归纳的整体研究方向聚类列表。"""
    try:
        from models import ResearchDirectionCluster
        async with get_db() as db:
            clusters = (await db.execute(
                select(ResearchDirectionCluster)
                .where(ResearchDirectionCluster.is_active == True)
                .order_by(ResearchDirectionCluster.similarity_group, ResearchDirectionCluster.id)
            )).scalars().all()
            return [_cluster_dict(c) for c in clusters]
    except Exception as exc:
        logger.error("list_direction_clusters error: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to load direction clusters")


@router.get("/clusters/{cluster_id}")
async def get_direction_cluster(cluster_id: int):
    """单个研究方向聚类详情。"""
    from models import ResearchDirectionCluster
    async with get_db() as db:
        cluster = (await db.execute(
            select(ResearchDirectionCluster).where(ResearchDirectionCluster.id == cluster_id)
        )).scalar_one_or_none()
        if not cluster:
            raise HTTPException(status_code=404, detail="Cluster not found")
        return _cluster_dict(cluster)


@router.post("/analyze")
async def trigger_direction_analysis():
    """手动触发研究方向分析（异步 task）。"""
    import uuid
    from config.database import get_redis, rkey
    import json as _json

    task_id = str(uuid.uuid4())
    try:
        r = await get_redis()
        task_payload = {
            "task_id": task_id,
            "skill": "research_trend",
            "input_text": "请分析当前所有项目成员的研究方向，归纳团队整体研究方向聚类，识别国际前沿动态，生成待激活的 idea 建议。",
            "user_id": "system:manual_trigger",
            "source": "api",
        }
        from config.database import rkey
        await r.lpush(rkey("task_queue"), _json.dumps(task_payload, ensure_ascii=False))
        return {"task_id": task_id, "status": "queued", "message": "研究方向分析任务已提交"}
    except Exception as exc:
        logger.error("trigger_direction_analysis error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/ideas")
async def list_direction_ideas(status: Optional[str] = "pending"):
    """待激活的研究方向 idea 列表。"""
    from models import ResearchDirectionIdea
    async with get_db() as db:
        query = select(ResearchDirectionIdea).order_by(ResearchDirectionIdea.created_at.desc())
        if status:
            query = query.where(ResearchDirectionIdea.status == status)
        ideas = (await db.execute(query)).scalars().all()
        return [_idea_dict(i) for i in ideas]


@router.post("/ideas/{idea_id}/activate")
async def activate_direction_idea(idea_id: int):
    """激活一个 idea → 转为正式研究方向。"""
    from models import ResearchDirectionIdea
    async with get_db() as db:
        idea = (await db.execute(
            select(ResearchDirectionIdea).where(ResearchDirectionIdea.id == idea_id)
        )).scalar_one_or_none()
        if not idea:
            raise HTTPException(status_code=404, detail="Idea not found")
        idea.status = "activated"
        # Create a formal ResearchDirection from this idea
        new_dir = ResearchDirection(
            title=idea.title,
            description=idea.description,
            source="ai_suggested",
            status="exploring",
            evidence=f"来源：待激活 idea #{idea_id}",
        )
        db.add(new_dir)
        await db.flush()
        await db.refresh(new_dir)
        return {"idea_id": idea_id, "direction_id": new_dir.id, "status": "activated"}


@router.post("/ideas/{idea_id}/dismiss")
async def dismiss_direction_idea(idea_id: int):
    """忽略一个 idea。"""
    from models import ResearchDirectionIdea
    async with get_db() as db:
        idea = (await db.execute(
            select(ResearchDirectionIdea).where(ResearchDirectionIdea.id == idea_id)
        )).scalar_one_or_none()
        if not idea:
            raise HTTPException(status_code=404, detail="Idea not found")
        idea.status = "dismissed"
        await db.flush()
        return {"idea_id": idea_id, "status": "dismissed"}


@router.get("/international")
async def list_international_trends():
    """国际相关研究追踪（来自 claw_research_trends 表）。"""
    from models import ResearchTrend
    async with get_db() as db:
        trends = (await db.execute(
            select(ResearchTrend)
            .order_by(ResearchTrend.relevance_score.desc(), ResearchTrend.discovered_at.desc())
            .limit(30)
        )).scalars().all()
        return [
            {
                "id": t.id,
                "domain": t.domain,
                "trend_title": t.trend_title,
                "summary": t.summary,
                "relevance_score": float(t.relevance_score) if t.relevance_score else 0,
                "matched_students": t.matched_students,
                "matched_directions": t.matched_directions,
                "source_urls": t.source_urls,
                "discovered_at": str(t.discovered_at) if t.discovered_at else None,
            }
            for t in trends
        ]
