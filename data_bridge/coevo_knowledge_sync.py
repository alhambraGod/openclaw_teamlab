"""
OpenClaw TeamLab — CoEvo → Knowledge Graph 增量同步管道

职责：
  以 cognalign_coevo_prod 为数据源头（事实真相），将最新团队活动数据
  增量同步到 openclaw_teamlab 的知识图谱（claw_knowledge_nodes/edges）。

同步四类数据（按数据价值排序）：
  1. 会议报告（post 阶段）— 导师点评、核心观点、学生总结
  2. 研究规划（completed）— 研究目标、周期节点
  3. 协作推荐（completed）— 合作方向、最佳搭档分析
  4. Agent 记忆（all types）— CAMA 已提炼的结构化记忆

增量策略：
  - Redis watermark key：`<prefix>:coevo_wm:<type>` 存储上次同步截止的
    updated_at ISO 字符串，仅拉取该时间点之后的新/变更记录。
  - 首次运行（无 watermark）默认拉取最近 30 天。

注意事项：
  - cognalign_coevo_prod 只读，任何时候绝不写入。
  - 写入 claw_knowledge_nodes/edges 时使用 upsert，保证幂等。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from sqlalchemy import select, and_

from config.coevo_db import get_coevo_db
from config.database import get_redis, rkey
from models.coevo import (
    CoevoMeeting, CoevoMeetingReport, CoevoUser, CoevoProject,
    CoevoProjectMember, CoevoResearchPlan, CoevoCollabRecommendation,
    CoevoAgentMemory,
)

logger = logging.getLogger("teamlab.data_bridge.coevo_knowledge_sync")

# watermark Redis key 后缀
_WM_REPORTS = "coevo_wm:meeting_reports"
_WM_PLANS   = "coevo_wm:research_plans"
_WM_COLLABS = "coevo_wm:collab_recs"
_WM_MEMORIES= "coevo_wm:agent_memories"

# 首次运行时回溯天数
DEFAULT_LOOKBACK_DAYS = 30
# 每类最多处理条数（防单次运行太久）
BATCH_LIMIT = 200


# ═══════════════════════════════════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════════════════════════════════

class CoevoKnowledgeSync:
    """
    将 cognalign_coevo_prod 的最新数据增量写入 openclaw_teamlab 知识图谱。
    每次调用 run() 仅处理上次水印之后的新增/变更记录。
    """

    def __init__(self) -> None:
        self._ks = None  # lazy-init KnowledgeStore

    async def run(self) -> dict[str, Any]:
        """执行完整增量同步，返回每类数据的处理统计。"""
        start = datetime.now(timezone.utc)
        logger.info("[CoevoSync] Starting incremental sync from cognalign_coevo_prod")

        # 预加载 user_id → name 映射（减少后续 N+1 查询）
        user_map = await self._load_user_map()
        project_map = await self._load_project_map()

        results: dict[str, int] = {}
        results["reports"]  = await self._sync_meeting_reports(user_map, project_map)
        results["plans"]    = await self._sync_research_plans(user_map, project_map)
        results["collabs"]  = await self._sync_collab_recs(user_map, project_map)
        results["memories"] = await self._sync_agent_memories(user_map, project_map)

        elapsed = round((datetime.now(timezone.utc) - start).total_seconds(), 1)
        total = sum(results.values())
        logger.info(
            "[CoevoSync] Done in %.1fs — reports=%d plans=%d collabs=%d memories=%d (total=%d nodes)",
            elapsed, results["reports"], results["plans"],
            results["collabs"], results["memories"], total,
        )
        return {"status": "completed", "elapsed_seconds": elapsed, **results, "total_nodes": total}

    # ─────────────────────────────────────────────────────────────────
    #  1. 会议报告同步（最有价值的信息来源）
    # ─────────────────────────────────────────────────────────────────

    async def _sync_meeting_reports(self, user_map: dict, project_map: dict) -> int:
        """同步 coevo 会议报告 → claw_knowledge_nodes。"""
        since = await self._get_watermark(_WM_REPORTS)
        latest_ts: datetime | None = None
        count = 0

        try:
            async with get_coevo_db() as db:
                rows = (await db.execute(
                    select(CoevoMeetingReport, CoevoMeeting, CoevoUser)
                    .join(CoevoMeeting, CoevoMeetingReport.meeting_id == CoevoMeeting.id)
                    .join(CoevoUser, CoevoMeetingReport.user_id == CoevoUser.id)
                    .where(
                        CoevoMeetingReport.phase == "post",
                        CoevoMeetingReport.updated_at > since,
                        CoevoMeeting.is_active == True,
                    )
                    .order_by(CoevoMeetingReport.updated_at.asc())
                    .limit(BATCH_LIMIT)
                )).all()

            ks = await self._get_ks()
            for report, meeting, user in rows:
                content_parts = []
                student_name = user.username or user.email or f"用户{user.id}"
                project_name = project_map.get(meeting.project_id, f"项目{meeting.project_id}")

                if report.core_viewpoints:
                    content_parts.append(f"核心观点：{report.core_viewpoints}")
                if report.teacher_comments:
                    content_parts.append(f"导师点评：{report.teacher_comments}")
                if report.teacher_suggestions:
                    content_parts.append(f"导师建议：{report.teacher_suggestions}")
                if report.student_summary:
                    content_parts.append(f"学生总结：{report.student_summary}")
                if report.issues_recorded:
                    content_parts.append(f"记录问题：{report.issues_recorded}")

                if not content_parts:
                    continue

                content = "\n\n".join(content_parts)
                title = f"{student_name} — {meeting.meeting_name or '会议'} 后会报告"

                node_id = await ks.upsert_node(
                    entity_type="person",
                    entity_id=student_name,
                    title=title,
                    content=content,
                    source="coevo",
                    importance=70,
                    metadata={
                        "coevo_report_id": report.id,
                        "meeting_id": meeting.id,
                        "meeting_name": meeting.meeting_name,
                        "meeting_time": str(meeting.meeting_time) if meeting.meeting_time else None,
                        "project_name": project_name,
                        "phase": "post",
                        "coevo_user_id": user.id,
                        "synced_at": datetime.now(timezone.utc).isoformat(),
                    },
                )

                # 建立学生 ↔ 项目关系边
                project_node_id = await ks.upsert_node(
                    entity_type="project",
                    entity_id=project_name,
                    title=f"项目：{project_name}",
                    content=f"{project_name} 项目成员及活动记录",
                    source="coevo",
                    importance=65,
                    metadata={"coevo_project_id": meeting.project_id},
                )
                if node_id and project_node_id:
                    await ks.add_edge(
                        from_node_id=node_id,
                        to_node_id=project_node_id,
                        relation="member_of",
                        weight=0.9,
                        bidirectional=False,
                        evidence=f"来自会议报告 {report.id}",
                    )

                count += 1
                if report.updated_at and (latest_ts is None or report.updated_at > latest_ts):
                    latest_ts = report.updated_at

        except Exception as exc:
            logger.error("[CoevoSync] meeting_reports sync failed: %s", exc, exc_info=True)

        if latest_ts:
            await self._set_watermark(_WM_REPORTS, latest_ts)
        return count

    # ─────────────────────────────────────────────────────────────────
    #  2. 研究规划同步
    # ─────────────────────────────────────────────────────────────────

    async def _sync_research_plans(self, user_map: dict, project_map: dict) -> int:
        """同步已完成的研究规划 → claw_knowledge_nodes。"""
        since = await self._get_watermark(_WM_PLANS)
        latest_ts: datetime | None = None
        count = 0

        try:
            async with get_coevo_db() as db:
                rows = (await db.execute(
                    select(CoevoResearchPlan, CoevoProject, CoevoUser)
                    .join(CoevoProject, CoevoResearchPlan.project_id == CoevoProject.id)
                    .join(CoevoUser, CoevoResearchPlan.creator_user_id == CoevoUser.id)
                    .where(
                        CoevoResearchPlan.status == "completed",
                        CoevoResearchPlan.updated_at > since,
                    )
                    .order_by(CoevoResearchPlan.updated_at.asc())
                    .limit(BATCH_LIMIT)
                )).all()

            ks = await self._get_ks()
            for plan, project, creator in rows:
                content_parts = [f"研究规划：{plan.plan_name}"]
                if plan.final_goal:
                    content_parts.append(f"最终目标：{plan.final_goal}")
                if plan.final_expected_effect:
                    content_parts.append(f"预期效果：{plan.final_expected_effect}")
                if plan.nodes:
                    try:
                        nodes_data = plan.nodes if isinstance(plan.nodes, list) else json.loads(plan.nodes)
                        # 提取每个研究周期的关键信息
                        cycle_summaries = []
                        for node in nodes_data[:5]:  # 最多取5个周期
                            if isinstance(node, dict):
                                cycle_goal = node.get("goal") or node.get("title") or ""
                                if cycle_goal:
                                    cycle_summaries.append(cycle_goal)
                        if cycle_summaries:
                            content_parts.append("研究周期目标：\n" + "\n".join(f"- {g}" for g in cycle_summaries))
                    except Exception:
                        pass

                content = "\n\n".join(content_parts)
                project_name = project.project_name
                creator_name = creator.username or creator.email

                node_id = await ks.upsert_node(
                    entity_type="research",
                    entity_id=f"research_plan_{plan.id}",
                    title=f"研究规划：{plan.plan_name} ({project_name})",
                    content=content,
                    source="coevo",
                    importance=75,
                    metadata={
                        "coevo_plan_id": plan.id,
                        "project_name": project_name,
                        "creator_name": creator_name,
                        "total_cycles": plan.total_cycles,
                        "synced_at": datetime.now(timezone.utc).isoformat(),
                    },
                )

                # 关联项目节点
                project_node_id = await ks.upsert_node(
                    entity_type="project",
                    entity_id=project_name,
                    title=f"项目：{project_name}",
                    content=f"{project_name} 项目研究方向与规划",
                    source="coevo",
                    importance=70,
                    metadata={"coevo_project_id": project.id},
                )
                if node_id and project_node_id:
                    await ks.add_edge(
                        from_node_id=project_node_id,
                        to_node_id=node_id,
                        relation="has_research_plan",
                        weight=1.0,
                        bidirectional=False,
                        evidence=f"来自研究规划 {plan.id}",
                    )

                count += 1
                if plan.updated_at and (latest_ts is None or plan.updated_at > latest_ts):
                    latest_ts = plan.updated_at

        except Exception as exc:
            logger.error("[CoevoSync] research_plans sync failed: %s", exc, exc_info=True)

        if latest_ts:
            await self._set_watermark(_WM_PLANS, latest_ts)
        return count

    # ─────────────────────────────────────────────────────────────────
    #  3. 协作推荐同步
    # ─────────────────────────────────────────────────────────────────

    async def _sync_collab_recs(self, user_map: dict, project_map: dict) -> int:
        """同步已完成的协作推荐 → claw_knowledge_nodes + 协作关系边。"""
        since = await self._get_watermark(_WM_COLLABS)
        latest_ts: datetime | None = None
        count = 0

        try:
            async with get_coevo_db() as db:
                rows = (await db.execute(
                    select(CoevoCollabRecommendation, CoevoProject, CoevoUser)
                    .join(CoevoProject, CoevoCollabRecommendation.project_id == CoevoProject.id)
                    .join(CoevoUser, CoevoCollabRecommendation.requester_user_id == CoevoUser.id)
                    .where(
                        CoevoCollabRecommendation.status == "completed",
                        CoevoCollabRecommendation.updated_at > since,
                    )
                    .order_by(CoevoCollabRecommendation.updated_at.asc())
                    .limit(BATCH_LIMIT)
                )).all()

            ks = await self._get_ks()
            for rec, project, requester in rows:
                requester_name = requester.username or requester.email
                project_name = project.project_name
                content_parts = [f"{requester_name} 在 {project_name} 的协作推荐"]

                if rec.collaboration_direction:
                    content_parts.append(f"合作方向：{rec.collaboration_direction}")
                if rec.collaboration_suggestion:
                    content_parts.append(f"合作建议：{rec.collaboration_suggestion}")
                if rec.expected_output:
                    content_parts.append(f"预期产出：{rec.expected_output}")

                # 解析最佳搭档分析
                partner_names: list[str] = []
                if rec.best_partner_analysis:
                    try:
                        bpa = rec.best_partner_analysis
                        if isinstance(bpa, str):
                            bpa = json.loads(bpa)
                        if isinstance(bpa, list):
                            for p in bpa[:3]:
                                if isinstance(p, dict):
                                    pname = p.get("name") or p.get("username") or ""
                                    if pname:
                                        partner_names.append(pname)
                                        reason = p.get("reason") or p.get("rationale") or ""
                                        if reason:
                                            content_parts.append(f"推荐搭档 {pname}：{reason[:200]}")
                    except Exception:
                        pass

                content = "\n\n".join(content_parts)
                requester_node_id = await ks.upsert_node(
                    entity_type="person",
                    entity_id=requester_name,
                    title=f"{requester_name} 协作推荐记录",
                    content=content,
                    source="coevo",
                    importance=72,
                    metadata={
                        "coevo_rec_id": rec.id,
                        "project_name": project_name,
                        "partner_names": partner_names,
                        "mode": rec.mode,
                        "synced_at": datetime.now(timezone.utc).isoformat(),
                    },
                )

                # 建立协作关系边
                for partner_name in partner_names:
                    partner_node_id = await ks.upsert_node(
                        entity_type="person",
                        entity_id=partner_name,
                        title=partner_name,
                        content=f"团队成员 {partner_name}",
                        source="coevo",
                        importance=60,
                        metadata={"project_name": project_name},
                    )
                    if requester_node_id and partner_node_id:
                        await ks.add_edge(
                            from_node_id=requester_node_id,
                            to_node_id=partner_node_id,
                            relation="recommended_collaborator",
                            weight=0.85,
                            bidirectional=True,
                            evidence=f"协作推荐记录 {rec.id}",
                        )

                count += 1
                if rec.updated_at and (latest_ts is None or rec.updated_at > latest_ts):
                    latest_ts = rec.updated_at

        except Exception as exc:
            logger.error("[CoevoSync] collab_recs sync failed: %s", exc, exc_info=True)

        if latest_ts:
            await self._set_watermark(_WM_COLLABS, latest_ts)
        return count

    # ─────────────────────────────────────────────────────────────────
    #  4. Agent 记忆同步（CAMA 已提炼的结构化记忆）
    # ─────────────────────────────────────────────────────────────────

    async def _sync_agent_memories(self, user_map: dict, project_map: dict) -> int:
        """同步 coevo agent_memories → claw_knowledge_nodes（高价值已提炼记忆）。"""
        since = await self._get_watermark(_WM_MEMORIES)
        latest_ts: datetime | None = None
        count = 0

        # 只同步高价值的记忆类型
        HIGH_VALUE_TYPES = {
            "per_person_summary",   # 对某人的综合总结 — 最有价值
            "teacher_feedback",     # 导师反馈
            "project_context",      # 项目背景
            "meeting_summary",      # 会议总结
        }

        try:
            async with get_coevo_db() as db:
                rows = (await db.execute(
                    select(CoevoAgentMemory, CoevoProject, CoevoUser)
                    .join(CoevoProject, CoevoAgentMemory.project_id == CoevoProject.id)
                    .outerjoin(CoevoUser, CoevoAgentMemory.user_id == CoevoUser.id)
                    .where(
                        CoevoAgentMemory.memory_type.in_(list(HIGH_VALUE_TYPES)),
                        CoevoAgentMemory.updated_at > since,
                        CoevoAgentMemory.content != None,
                    )
                    .order_by(
                        CoevoAgentMemory.relevance_score.desc(),
                        CoevoAgentMemory.updated_at.asc(),
                    )
                    .limit(BATCH_LIMIT)
                )).all()

            ks = await self._get_ks()
            for memory, project, user in rows:
                if not memory.content or len(memory.content.strip()) < 30:
                    continue

                project_name = project.project_name
                user_name = (user.username or user.email) if user else None

                # 根据 memory_type 设置重要性和实体类型
                importance_map = {
                    "per_person_summary": 80,
                    "teacher_feedback":   78,
                    "project_context":    72,
                    "meeting_summary":    65,
                }
                entity_type_map = {
                    "per_person_summary": "person",
                    "teacher_feedback":   "person",
                    "project_context":    "project",
                    "meeting_summary":    "project",
                }
                importance = importance_map.get(memory.memory_type, 60)
                entity_type = entity_type_map.get(memory.memory_type, "insight")

                if entity_type == "person" and user_name:
                    entity_id = user_name
                    title = f"{user_name} — {memory.memory_type} ({project_name})"
                else:
                    entity_id = project_name
                    title = f"{project_name} — {memory.memory_type}"

                await ks.upsert_node(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    title=title,
                    content=memory.content,
                    source="coevo",
                    importance=importance,
                    metadata={
                        "coevo_memory_id": memory.id,
                        "memory_type": memory.memory_type,
                        "project_name": project_name,
                        "relevance_score": memory.relevance_score,
                        "reference_count": memory.reference_count,
                        "cycle_id": memory.cycle_id,
                        "synced_at": datetime.now(timezone.utc).isoformat(),
                    },
                )

                count += 1
                if memory.updated_at and (latest_ts is None or memory.updated_at > latest_ts):
                    latest_ts = memory.updated_at

        except Exception as exc:
            logger.error("[CoevoSync] agent_memories sync failed: %s", exc, exc_info=True)

        if latest_ts:
            await self._set_watermark(_WM_MEMORIES, latest_ts)
        return count

    # ─────────────────────────────────────────────────────────────────
    #  辅助方法：watermark / 预加载
    # ─────────────────────────────────────────────────────────────────

    async def _get_watermark(self, wm_key: str) -> datetime:
        """读取 Redis watermark，无则返回 DEFAULT_LOOKBACK_DAYS 天前。"""
        try:
            r = await get_redis()
            val = await r.get(rkey(wm_key))
            if val:
                ts = val.decode() if isinstance(val, bytes) else str(val)
                return datetime.fromisoformat(ts)
        except Exception as exc:
            logger.debug("[CoevoSync] Watermark read failed (%s): %s", wm_key, exc)
        fallback = datetime.now(timezone.utc) - timedelta(days=DEFAULT_LOOKBACK_DAYS)
        return fallback

    async def _set_watermark(self, wm_key: str, ts: datetime) -> None:
        """将最新处理时间写入 Redis watermark（TTL 90 天）。"""
        try:
            r = await get_redis()
            # 确保带时区
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            await r.set(rkey(wm_key), ts.isoformat(), ex=60 * 60 * 24 * 90)
        except Exception as exc:
            logger.warning("[CoevoSync] Watermark write failed (%s): %s", wm_key, exc)

    async def _get_ks(self):
        """懒加载 KnowledgeStore 实例。"""
        if self._ks is None:
            from knowledge.store import KnowledgeStore
            self._ks = KnowledgeStore()
        return self._ks

    async def _load_user_map(self) -> dict[int, str]:
        """预加载 coevo 用户 id → 显示名，减少 N+1 查询。"""
        try:
            async with get_coevo_db() as db:
                rows = (await db.execute(
                    select(CoevoUser.id, CoevoUser.username, CoevoUser.email)
                    .where(CoevoUser.is_active == True)
                )).all()
                return {
                    uid: (name or email or f"用户{uid}")
                    for uid, name, email in rows
                }
        except Exception as exc:
            logger.warning("[CoevoSync] User map load failed: %s", exc)
            return {}

    async def _load_project_map(self) -> dict[int, str]:
        """预加载 coevo 项目 id → 项目名。"""
        try:
            async with get_coevo_db() as db:
                rows = (await db.execute(
                    select(CoevoProject.id, CoevoProject.project_name)
                    .where(CoevoProject.is_active == True)
                )).all()
                return {pid: (name or f"项目{pid}") for pid, name in rows}
        except Exception as exc:
            logger.warning("[CoevoSync] Project map load failed: %s", exc)
            return {}

    # ─────────────────────────────────────────────────────────────────
    #  获取 coevo 团队活跃度统计（供 Evolver 使用）
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    async def get_team_activity_stats(days: int = 7) -> dict[str, Any]:
        """
        从 cognalign_coevo_prod 采集最近 N 天的团队活跃度指标。
        供 Evolver 纳入系统健康分析。
        """
        stats: dict[str, Any] = {}
        try:
            from sqlalchemy import func, text as sa_text
            async with get_coevo_db() as db:
                # 会议数 & 已完成率
                meeting_row = (await db.execute(
                    sa_text("""
                        SELECT
                            COUNT(*) AS total,
                            SUM(status='completed') AS completed,
                            COUNT(DISTINCT project_id) AS active_projects
                        FROM meetings
                        WHERE is_active=1
                          AND meeting_time >= DATE_SUB(NOW(), INTERVAL :days DAY)
                    """),
                    {"days": days},
                )).mappings().first()
                if meeting_row:
                    stats["meetings_total"] = int(meeting_row["total"] or 0)
                    stats["meetings_completed"] = int(meeting_row["completed"] or 0)
                    stats["active_projects"] = int(meeting_row["active_projects"] or 0)

                # 报告提交率
                report_row = (await db.execute(
                    sa_text("""
                        SELECT
                            COUNT(*) AS total,
                            SUM(status='submitted') AS submitted,
                            SUM(status='summarized') AS summarized
                        FROM meeting_reports
                        WHERE created_at >= DATE_SUB(NOW(), INTERVAL :days DAY)
                    """),
                    {"days": days},
                )).mappings().first()
                if report_row:
                    total_r = int(report_row["total"] or 0)
                    submitted = int(report_row["submitted"] or 0) + int(report_row["summarized"] or 0)
                    stats["report_submission_rate"] = round(submitted / max(1, total_r) * 100, 1)
                    stats["reports_total"] = total_r
                    stats["reports_submitted"] = submitted

                # 研究规划完成数
                plan_row = (await db.execute(
                    sa_text("""
                        SELECT COUNT(*) AS completed
                        FROM research_plans
                        WHERE status='completed'
                          AND updated_at >= DATE_SUB(NOW(), INTERVAL :days DAY)
                    """),
                    {"days": days},
                )).mappings().first()
                if plan_row:
                    stats["research_plans_completed"] = int(plan_row["completed"] or 0)

                # 协作推荐完成数
                collab_row = (await db.execute(
                    sa_text("""
                        SELECT COUNT(*) AS completed
                        FROM collaboration_recommendations
                        WHERE status='completed'
                          AND updated_at >= DATE_SUB(NOW(), INTERVAL :days DAY)
                    """),
                    {"days": days},
                )).mappings().first()
                if collab_row:
                    stats["collabs_completed"] = int(collab_row["completed"] or 0)

                # 新增 Agent 记忆数（系统活跃度指标）
                mem_row = (await db.execute(
                    sa_text("""
                        SELECT COUNT(*) AS cnt
                        FROM agent_memories
                        WHERE created_at >= DATE_SUB(NOW(), INTERVAL :days DAY)
                    """),
                    {"days": days},
                )).mappings().first()
                if mem_row:
                    stats["new_agent_memories"] = int(mem_row["cnt"] or 0)

        except Exception as exc:
            logger.warning("[CoevoSync] get_team_activity_stats failed: %s", exc)

        return stats
