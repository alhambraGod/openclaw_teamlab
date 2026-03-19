"""
OpenClaw TeamLab — Team Context Snapshot
Builds a concise Markdown snapshot of the current cognalign-coevo team state.
Used to inject live data into LLM prompts so the model can answer factual
questions (member count, collaborations, research directions) correctly.

Cache: Redis, TTL = 30 minutes.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import text as sa_text

from config.coevo_db import get_coevo_db

logger = logging.getLogger("teamlab.data_bridge.team_context")

_CACHE_KEY = "teamlab:team_context_snapshot"
_CACHE_TTL = 1800  # 30 minutes


async def get_team_snapshot(force_refresh: bool = False) -> str:
    """Return a Markdown snapshot of the team, cached in Redis for 30 min."""
    # Try cache first
    if not force_refresh:
        try:
            from config.database import get_redis
            r = await get_redis()
            cached = await r.get(_CACHE_KEY)
            if cached:
                return cached
        except Exception as exc:
            logger.debug("Cache miss/error: %s", exc)

    # Build from DB
    try:
        snapshot = await _build_snapshot()
    except Exception as exc:
        logger.error("Failed to build team snapshot: %s", exc, exc_info=True)
        return "(团队数据暂时无法获取)"

    # Store in cache
    try:
        from config.database import get_redis
        r = await get_redis()
        await r.set(_CACHE_KEY, snapshot, ex=_CACHE_TTL)
    except Exception as exc:
        logger.debug("Cache write error: %s", exc)

    return snapshot


async def invalidate_cache():
    """Force next call to rebuild from DB."""
    try:
        from config.database import get_redis
        r = await get_redis()
        await r.delete(_CACHE_KEY)
    except Exception:
        pass


async def _build_snapshot() -> str:
    """Query CoEvo DB and construct a Markdown context block."""
    lines: list[str] = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines.append(f"## 当前团队数据（来自 cognalign-coevo，{now} 更新）\n")

    async with get_coevo_db() as db:

        # ── 1. Team size by role ──────────────────────────────────────────
        r = await db.execute(sa_text(
            "SELECT role, COUNT(*) AS cnt FROM users WHERE is_active=1 GROUP BY role"
        ))
        counts = {row["role"]: row["cnt"] for row in r.mappings().all()}
        lines.append("### 团队规模")
        parts = []
        if counts.get("student"):
            parts.append(f"学生 {counts['student']} 人")
        if counts.get("teacher"):
            parts.append(f"教师/导师 {counts['teacher']} 人")
        if counts.get("researcher"):
            parts.append(f"研究员 {counts['researcher']} 人")
        if counts.get("pm"):
            parts.append(f"项目管理 {counts['pm']} 人")
        total = sum(counts.values())
        lines.append(f"- 总计 {total} 人：{' | '.join(parts)}")
        lines.append("")

        # ── 2. Active projects ────────────────────────────────────────────
        r = await db.execute(sa_text(
            "SELECT id, project_name FROM projects WHERE is_active=1 ORDER BY id"
        ))
        projects = r.mappings().all()
        if projects:
            lines.append("### 活跃项目")
            for p in projects:
                lines.append(f"- [{p['id']}] {p['project_name']}")
            lines.append("")

        # ── 3. Teachers / PI list ─────────────────────────────────────────
        r = await db.execute(sa_text(
            "SELECT id, username, bio FROM users WHERE is_active=1 AND role='teacher' ORDER BY username"
        ))
        teachers = r.mappings().all()
        if teachers:
            lines.append("### 导师/教师")
            for t in teachers:
                bio_snippet = (t["bio"] or "")[:80]
                lines.append(f"- **{t['username']}** (id={t['id']}){': ' + bio_snippet if bio_snippet else ''}")
            lines.append("")

        # ── 4. Students with project + goals ─────────────────────────────
        r = await db.execute(sa_text("""
            SELECT u.id, u.username, u.bio,
                   pm.quarterly_goal, pm.short_term_goal, p.project_name
            FROM users u
            LEFT JOIN project_members pm ON pm.user_id = u.id
            LEFT JOIN projects p ON p.id = pm.project_id AND p.is_active = 1
            WHERE u.is_active = 1 AND u.role = 'student'
            ORDER BY u.username
        """))
        claw_students = r.mappings().all()
        if claw_students:
            lines.append("### 学生成员（含项目和目标）")
            for s in claw_students:
                goal = (s["quarterly_goal"] or s["short_term_goal"] or s["bio"] or "")[:120]
                proj = s["project_name"] or "未分配项目"
                lines.append(f"- **{s['username']}** (id={s['id']}) [{proj}]")
                if goal:
                    lines.append(f"  目标: {goal}")
            lines.append("")

        # ── 5. Recent collaboration recommendations ───────────────────────
        r = await db.execute(sa_text("""
            SELECT cr.requester_user_id, cr.target_user_ids,
                   cr.collaboration_suggestion,
                   u.username AS requester_name
            FROM collaboration_recommendations cr
            JOIN users u ON u.id = cr.requester_user_id
            WHERE cr.status = 'completed'
            ORDER BY cr.created_at DESC LIMIT 10
        """))
        collabs = r.mappings().all()
        if collabs:
            lines.append("### 近期协作推荐（已完成）")
            # Build id→name map for target resolution
            all_ids = set()
            for c in collabs:
                try:
                    tids = json.loads(c["target_user_ids"]) if isinstance(c["target_user_ids"], str) else (c["target_user_ids"] or [])
                    all_ids.update(tids)
                except Exception:
                    pass
            id_name: dict[int, str] = {}
            if all_ids:
                id_list = ",".join(str(i) for i in all_ids)
                r2 = await db.execute(sa_text(
                    f"SELECT id, username FROM users WHERE id IN ({id_list})"
                ))
                id_name = {row["id"]: row["username"] for row in r2.mappings().all()}

            for c in collabs:
                try:
                    tids = json.loads(c["target_user_ids"]) if isinstance(c["target_user_ids"], str) else (c["target_user_ids"] or [])
                    target_names = [id_name.get(tid, f"id:{tid}") for tid in tids]
                except Exception:
                    target_names = []
                requester = c["requester_name"]
                if target_names:
                    partners = "、".join(target_names)
                    suggestion = (c["collaboration_suggestion"] or "")[:150]
                    lines.append(f"- **{requester}** → 推荐与 **{partners}** 合作")
                    if suggestion:
                        lines.append(f"  建议: {suggestion}")
            lines.append("")

        # ── 6. Research plans ─────────────────────────────────────────────
        r = await db.execute(sa_text("""
            SELECT rp.plan_name, rp.final_goal, rp.status, u.username AS creator_name
            FROM research_plans rp
            JOIN users u ON u.id = rp.creator_user_id
            WHERE rp.status IN ('active', 'completed')
            ORDER BY rp.created_at DESC LIMIT 10
        """))
        plans = r.mappings().all()
        if plans:
            lines.append("### 研究计划")
            for p in plans:
                goal = (p["final_goal"] or p["plan_name"] or "")[:120]
                lines.append(f"- **{p['creator_name']}**（{p['status']}）: {goal}")
            lines.append("")

    return "\n".join(lines)
