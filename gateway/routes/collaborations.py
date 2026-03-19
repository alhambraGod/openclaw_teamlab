"""
OpenClaw TeamLab — Collaboration Routes
Recommendations, status updates, and network graph data.
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func

from config.database import get_db
from models import CollaborationRecommendation, Student

logger = logging.getLogger("teamlab.routes.collaborations")
router = APIRouter(prefix="/collaborations", tags=["collaborations"])


class StatusUpdate(BaseModel):
    status: str  # accepted, dismissed, in_progress, completed


def _collab_dict(c: CollaborationRecommendation, student_a: dict | None = None, student_b: dict | None = None) -> dict:
    result = {
        "id": c.id,
        "student_a_id": c.student_a_id,
        "student_b_id": c.student_b_id,
        "complementarity_score": float(c.complementarity_score) if c.complementarity_score else None,
        "overlap_score": float(c.overlap_score) if c.overlap_score else None,
        "research_idea": c.research_idea,
        "rationale": c.rationale,
        "status": c.status,
        "created_at": str(c.created_at) if c.created_at else None,
        "updated_at": str(c.updated_at) if c.updated_at else None,
    }
    if student_a:
        result["student_a"] = student_a
    if student_b:
        result["student_b"] = student_b
    return result


def _student_brief(s: Student) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "research_area": s.research_area,
        "avatar_url": s.avatar_url,
    }


# ── Routes ──

@router.get("")
async def list_collaborations():
    """List collaboration recommendations with student details."""
    async with get_db() as db:
        collabs = (await db.execute(
            select(CollaborationRecommendation)
            .order_by(CollaborationRecommendation.created_at.desc())
        )).scalars().all()

        # Batch load referenced claw_students
        student_ids = set()
        for c in collabs:
            student_ids.add(c.student_a_id)
            student_ids.add(c.student_b_id)

        students_map: dict[int, Student] = {}
        if student_ids:
            claw_students = (await db.execute(
                select(Student).where(Student.id.in_(student_ids))
            )).scalars().all()
            students_map = {s.id: s for s in claw_students}

        return [
            _collab_dict(
                c,
                student_a=_student_brief(students_map[c.student_a_id]) if c.student_a_id in students_map else None,
                student_b=_student_brief(students_map[c.student_b_id]) if c.student_b_id in students_map else None,
            )
            for c in collabs
        ]


@router.post("/{collab_id}/status")
async def update_collab_status(collab_id: int, body: StatusUpdate):
    """Update the status of a collaboration recommendation."""
    async with get_db() as db:
        collab = (await db.execute(
            select(CollaborationRecommendation)
            .where(CollaborationRecommendation.id == collab_id)
        )).scalar_one_or_none()
        if not collab:
            raise HTTPException(status_code=404, detail="Collaboration not found")

        collab.status = body.status
        await db.flush()
        await db.refresh(collab)
        return _collab_dict(collab)


@router.get("/network")
async def collaboration_network():
    """Multi-layer network graph: projects → members → direction links.
    Returns nodes (project/student/direction types) and typed edges.
    """
    try:
        from data_bridge import queries as Q
        from models import ResearchDirectionCluster
        from sqlalchemy import select

        # ── Members from CoEvo ──
        coevo_members = await Q.get_members_with_goals()
        # member projects mapping: user_id → list of project info
        all_projects = await Q.get_all_projects()
        project_map = {p["id"]: p for p in all_projects}

        # student project membership: coevo_user_id → [project_id, ...]
        member_project_map: dict[int, list[int]] = {}
        for m in coevo_members:
            uid = m["id"]
            # get their project memberships
            if uid not in member_project_map:
                member_project_map[uid] = []
            # project_id is embedded in get_members_with_goals result via _map_rank
            # We need to re-query members with project info

        # Fetch full membership info per project
        student_nodes = []
        student_projects: dict[int, set[int]] = {}
        seen_students: dict[int, dict] = {}
        for proj in all_projects:
            members = await Q.get_students_in_project(proj["id"])
            for m in members:
                uid = m["id"]
                if uid not in seen_students:
                    seen_students[uid] = {
                        "id": uid,
                        "name": m.get("display_name") or m["username"],
                        "research_area": m.get("bio", ""),
                        "quarterly_goal": m.get("quarterly_goal", ""),
                    }
                    student_projects[uid] = set()
                student_projects[uid].add(proj["id"])

        # ── OpenClaw capability scores per student (if synced) ──
        oc_scores: dict[int, float] = {}
        try:
            from config.database import get_db
            from models import Student, CoevoStudentLink, CapabilityScore
            async with get_db() as db:
                link_rows = (await db.execute(
                    select(CoevoStudentLink.coevo_user_id, CoevoStudentLink.student_id)
                )).all()
                link_map = {row.coevo_user_id: row.student_id for row in link_rows}
                if link_map:
                    score_rows = (await db.execute(
                        select(CapabilityScore.student_id, func.avg(CapabilityScore.score).label("avg"))
                        .where(CapabilityScore.student_id.in_(list(link_map.values())))
                        .group_by(CapabilityScore.student_id)
                    )).all()
                    oc_student_scores = {r.student_id: float(r.avg) for r in score_rows}
                    for coevo_id, oc_id in link_map.items():
                        if oc_id in oc_student_scores:
                            oc_scores[coevo_id] = oc_student_scores[oc_id]
        except Exception:
            pass

        # Build student nodes
        for uid, info in seen_students.items():
            proj_list = list(student_projects.get(uid, set()))
            student_nodes.append({
                "id": f"student_{uid}",
                "type": "student",
                "coevo_user_id": uid,
                "name": info["name"],
                "research_area": info["research_area"][:100] if info["research_area"] else "",
                "projects": proj_list,
                "primary_project": proj_list[0] if proj_list else None,
                "capability_avg": oc_scores.get(uid, 0),
                "quarterly_goal": info["quarterly_goal"][:80] if info["quarterly_goal"] else "",
            })

        # ── Project nodes ──
        project_nodes = [
            {
                "id": f"project_{p['id']}",
                "type": "project",
                "project_id": p["id"],
                "name": p["project_name"],
                "member_count": p.get("member_count", 0),
            }
            for p in all_projects
        ]

        # ── Research direction cluster nodes ──
        direction_nodes = []
        try:
            from config.database import get_db
            async with get_db() as db:
                clusters = (await db.execute(
                    select(ResearchDirectionCluster)
                    .where(ResearchDirectionCluster.is_active == True)
                )).scalars().all()
                direction_nodes = [
                    {
                        "id": f"dir_{c.id}",
                        "type": "direction",
                        "cluster_id": c.id,
                        "topic": c.topic,
                        "keywords": c.keywords or [],
                        "similarity_group": c.similarity_group,
                    }
                    for c in clusters
                ]
        except Exception:
            pass

        # ── Edges ──
        edges = []

        # collab edges from openclaw (student ↔ student)
        try:
            from config.database import get_db
            from models import CollaborationRecommendation, CoevoStudentLink
            async with get_db() as db:
                collabs = (await db.execute(
                    select(CollaborationRecommendation)
                    .where(CollaborationRecommendation.status != "dismissed")
                )).scalars().all()
                link_rows = (await db.execute(select(CoevoStudentLink))).scalars().all()
                oc_to_coevo = {lnk.student_id: lnk.coevo_user_id for lnk in link_rows}

            for c in collabs:
                src_coevo = oc_to_coevo.get(c.student_a_id)
                tgt_coevo = oc_to_coevo.get(c.student_b_id)
                if src_coevo and tgt_coevo:
                    edges.append({
                        "source": f"student_{src_coevo}",
                        "target": f"student_{tgt_coevo}",
                        "type": "collab",
                        "score": float(c.complementarity_score) if c.complementarity_score else 0.5,
                        "label": c.research_idea[:40] if c.research_idea else "",
                    })
        except Exception:
            pass

        # CoEvo collab recommendations edges
        coevo_collabs = await Q.get_all_collabs(limit=50)
        for cc in coevo_collabs:
            req_id = cc.get("requester_user_id")
            target_ids = cc.get("target_user_ids") or []
            if not isinstance(target_ids, list):
                try:
                    import json as _json
                    target_ids = _json.loads(target_ids) if isinstance(target_ids, str) else []
                except Exception:
                    target_ids = []
            for tgt_id in target_ids:
                if req_id and tgt_id:
                    edges.append({
                        "source": f"student_{req_id}",
                        "target": f"student_{tgt_id}",
                        "type": "collab",
                        "score": 0.7,
                        "label": (cc.get("collaboration_direction") or "")[:40],
                    })

        # direction links (student → direction cluster)
        for dn in direction_nodes:
            cluster_id = dn["cluster_id"]
            # find related claw_students from cluster data
            try:
                from config.database import get_db
                from models import ResearchDirectionCluster
                async with get_db() as db:
                    cluster = (await db.execute(
                        select(ResearchDirectionCluster).where(ResearchDirectionCluster.id == cluster_id)
                    )).scalar_one_or_none()
                    if cluster and cluster.related_students:
                        for stu in (cluster.related_students or []):
                            edges.append({
                                "source": f"student_{stu.get('id')}",
                                "target": dn["id"],
                                "type": "direction_link",
                                "score": 0.6,
                            })
            except Exception:
                pass

        # cross-project edges (claw_students sharing same research direction)
        cross_done: set = set()
        for i, sn_i in enumerate(student_nodes):
            for sn_j in student_nodes[i+1:]:
                # claw_students from different projects who share a project?
                i_projs = set(sn_i.get("projects", []))
                j_projs = set(sn_j.get("projects", []))
                if i_projs and j_projs and not i_projs.intersection(j_projs):
                    key = tuple(sorted([sn_i["id"], sn_j["id"]]))
                    if key not in cross_done:
                        # Only add if they have direction overlap (check via direction edges)
                        edges.append({
                            "source": sn_i["id"],
                            "target": sn_j["id"],
                            "type": "cross_project",
                            "score": 0.4,
                        })
                        cross_done.add(key)

        # Stats
        collab_edges = [e for e in edges if e["type"] == "collab"]
        cross_edges = [e for e in edges if e["type"] == "cross_project"]
        stats = {
            "total_students": len(student_nodes),
            "total_projects": len(project_nodes),
            "total_directions": len(direction_nodes),
            "collab_links": len(collab_edges),
            "cross_project_links": len(cross_edges),
        }

        return {
            "nodes": project_nodes + student_nodes + direction_nodes,
            "edges": edges,
            "stats": stats,
        }
    except Exception as exc:
        import traceback
        logger.error("collaboration_network error: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Failed to build network: {exc}")
