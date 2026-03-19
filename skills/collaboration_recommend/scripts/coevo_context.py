"""
协作推荐 — CoEvo 上下文获取工具

从 cognalign_coevo_prod 读取指定学生的完整上下文，包括：
- 基本资料（bio、项目参与、目标）
- 会议报告（key_blockers、任务项、teacher_suggestions）
- 历史协作推荐（AI 分析的合作建议）
- OpenClaw 能力评分（如已同步）

供 LLM 在生成协作推荐时作为数据依据，而非仅凭抽象得分。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from config.coevo_db import get_coevo_db
from models.coevo import (
    CoevoUser, CoevoProject, CoevoProjectMember,
    CoevoMeeting, CoevoMeetingReport,
    CoevoCollabRecommendation,
)
from sqlalchemy import select, or_

logger = logging.getLogger(__name__)


async def fetch_collaboration_context(student_name: str) -> dict:
    """
    根据学生姓名从 CoEvo 获取完整的协作上下文数据。

    返回结构：
    {
        "found": bool,
        "profile": {id, username, email, bio, projects: [...]},
        "blockers": ["近期遇到的障碍1", ...],
        "task_items": ["近期任务项1", ...],
        "teacher_suggestions": ["导师建议1", ...],
        "claw_meeting_insights": ["AI 生成的会议洞察1", ...],
        "collab_history": [
            {
                "direction": "合作方向",
                "suggestion": "具体合作建议",
                "partner_analysis": "合作伙伴分析",
                "project_name": "所在项目"
            }, ...
        ],
        "claw_capability_scores": {"论文阅读": 72.5, ...},  // 如已在 OpenClaw 中评估
        "summary": "一段给 LLM 的文字摘要"
    }
    """
    result: dict[str, Any] = {
        "found": False,
        "profile": None,
        "blockers": [],
        "task_items": [],
        "teacher_suggestions": [],
        "claw_meeting_insights": [],
        "collab_history": [],
        "claw_capability_scores": {},
        "summary": f"未找到学生 {student_name} 的数据",
    }

    try:
        async with get_coevo_db() as db:
            # Step 1: 查找学生（支持模糊匹配：用户名、显示名）
            name_pattern = f"%{student_name.strip()}%"
            user_rows = (await db.execute(
                select(CoevoUser).where(
                    or_(
                        CoevoUser.username.like(name_pattern),
                        CoevoUser.email.like(name_pattern),
                    )
                ).limit(3)
            )).scalars().all()

            # 也在项目成员 display_name 中查
            member_match_rows = (await db.execute(
                select(CoevoUser, CoevoProjectMember)
                .join(CoevoProjectMember, CoevoProjectMember.user_id == CoevoUser.id)
                .where(CoevoProjectMember.display_name.like(name_pattern))
                .limit(3)
            )).all()

            # 合并候选用户（去重）
            candidates: dict[int, CoevoUser] = {}
            for u in user_rows:
                candidates[u.id] = u
            for u, _ in member_match_rows:
                candidates[u.id] = u

            if not candidates:
                result["summary"] = f"在 CoEvo 中未找到名为「{student_name}」的学生，将仅基于能力矩阵推荐。"
                return result

            # 取第一个匹配（最佳候选）
            user = next(iter(candidates.values()))
            coevo_user_id = user.id

            # Step 2: 获取项目参与情况
            member_rows = (await db.execute(
                select(CoevoProjectMember, CoevoProject)
                .join(CoevoProject, CoevoProjectMember.project_id == CoevoProject.id)
                .where(CoevoProjectMember.user_id == coevo_user_id)
            )).all()

            projects = []
            for member, project in member_rows:
                projects.append({
                    "project_id": project.id,
                    "project_name": project.project_name,
                    "role": member.project_role,
                    "display_name": member.display_name,
                    "quarterly_goal": member.quarterly_goal or "",
                    "short_term_goal": member.short_term_goal or "",
                })

            result["profile"] = {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "bio": user.bio or "",
                "projects": projects,
            }
            result["found"] = True

            # Step 3: 获取 pre-meeting 报告（blockers、任务项）
            pre_rows = (await db.execute(
                select(CoevoMeetingReport, CoevoMeeting)
                .join(CoevoMeeting, CoevoMeetingReport.meeting_id == CoevoMeeting.id)
                .where(
                    CoevoMeetingReport.user_id == coevo_user_id,
                    CoevoMeetingReport.phase == "pre",
                    CoevoMeetingReport.status == "submitted",
                )
                .order_by(CoevoMeeting.meeting_time.desc())
                .limit(8)
            )).all()

            for report, meeting in pre_rows:
                if report.key_blockers:
                    result["blockers"].append(
                        f"[{meeting.meeting_name}] {report.key_blockers}"
                    )
                if report.task_items:
                    result["task_items"].append(
                        f"[{meeting.meeting_name}] {report.task_items}"
                    )

            # Step 4: 获取 post-meeting AI 报告（洞察、导师建议）
            post_rows = (await db.execute(
                select(CoevoMeetingReport, CoevoMeeting)
                .join(CoevoMeeting, CoevoMeetingReport.meeting_id == CoevoMeeting.id)
                .where(
                    CoevoMeetingReport.user_id == coevo_user_id,
                    CoevoMeetingReport.phase == "post",
                )
                .order_by(CoevoMeeting.meeting_time.desc())
                .limit(8)
            )).all()

            for report, meeting in post_rows:
                if report.teacher_suggestions:
                    result["teacher_suggestions"].append(
                        f"[{meeting.meeting_name}] {report.teacher_suggestions}"
                    )
                if report.core_viewpoints:
                    result["claw_meeting_insights"].append(
                        f"[{meeting.meeting_name}] 核心观点: {report.core_viewpoints}"
                    )
                if report.issues_recorded:
                    result["claw_meeting_insights"].append(
                        f"[{meeting.meeting_name}] 记录问题: {report.issues_recorded}"
                    )

            # Step 5: 获取历史协作推荐（此学生作为 requester）
            collab_rows = (await db.execute(
                select(CoevoCollabRecommendation, CoevoProject)
                .join(CoevoProject, CoevoCollabRecommendation.project_id == CoevoProject.id)
                .where(CoevoCollabRecommendation.requester_user_id == coevo_user_id)
                .order_by(CoevoCollabRecommendation.created_at.desc())
                .limit(5)
            )).all()

            for collab, project in collab_rows:
                result["collab_history"].append({
                    "direction": collab.collaboration_direction or "",
                    "suggestion": collab.collaboration_suggestion or "",
                    "partner_analysis": str(collab.best_partner_analysis or ""),
                    "project_name": project.project_name,
                })

        # Step 6: 从 OpenClaw DB 查能力评分（如已同步）
        try:
            from config.database import get_db_pool
            pool = await get_db_pool()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    # 先找 openclaw student_id via claw_coevo_student_links
                    await cur.execute(
                        "SELECT student_id FROM claw_coevo_student_links WHERE coevo_user_id = %s LIMIT 1",
                        (coevo_user_id,)
                    )
                    link_row = await cur.fetchone()
                    if link_row:
                        student_id = link_row[0]
                        await cur.execute(
                            """SELECT cd.name, cs.score
                               FROM claw_capability_scores cs
                               JOIN claw_capability_dimensions cd ON cs.dimension_id = cd.id
                               WHERE cs.student_id = %s
                               ORDER BY cs.assessed_at DESC""",
                            (student_id,)
                        )
                        score_rows = await cur.fetchall()
                        seen = set()
                        for dim_name, score in score_rows:
                            if dim_name not in seen:
                                result["claw_capability_scores"][dim_name] = float(score)
                                seen.add(dim_name)
        except Exception as e:
            logger.debug("Could not load openclaw capability scores: %s", e)

    except Exception as e:
        logger.error("fetch_collaboration_context failed for '%s': %s", student_name, e, exc_info=True)
        result["summary"] = f"查询 {student_name} 数据时出错: {e}"
        return result

    # Step 7: 构建给 LLM 的文字摘要
    lines = [f"=== {student_name} 的 CoEvo 数据摘要 ==="]

    if result["profile"]:
        p = result["profile"]
        proj_names = [proj["project_name"] for proj in p["projects"]]
        lines.append(f"\n参与项目：{', '.join(proj_names) if proj_names else '无'}")
        if p["bio"]:
            lines.append(f"研究背景：{p['bio'][:200]}")
        for proj in p["projects"]:
            if proj.get("quarterly_goal"):
                lines.append(f"季度目标（{proj['project_name']}）：{proj['quarterly_goal'][:150]}")
            if proj.get("short_term_goal"):
                lines.append(f"近期目标（{proj['project_name']}）：{proj['short_term_goal'][:150]}")

    if result["blockers"]:
        lines.append("\n近期遇到的障碍：")
        for b in result["blockers"][:4]:
            lines.append(f"  • {b[:200]}")

    if result["teacher_suggestions"]:
        lines.append("\n导师给出的建议：")
        for s in result["teacher_suggestions"][:4]:
            lines.append(f"  • {s[:200]}")

    if result["claw_meeting_insights"]:
        lines.append("\n会议 AI 洞察：")
        for ins in result["claw_meeting_insights"][:4]:
            lines.append(f"  • {ins[:200]}")

    if result["collab_history"]:
        lines.append("\nCoEvo 系统已生成的协作建议：")
        for ch in result["collab_history"][:3]:
            lines.append(f"  • 方向: {ch['direction'][:100]}；建议: {ch['suggestion'][:150]}")

    if result["claw_capability_scores"]:
        lines.append("\nOpenClaw 能力评分：")
        for dim, score in sorted(result["claw_capability_scores"].items(), key=lambda x: -x[1]):
            bar = "█" * int(score / 10) + "░" * (10 - int(score / 10))
            lines.append(f"  {dim}: {score:.1f} [{bar}]")

    result["summary"] = "\n".join(lines)
    return result
