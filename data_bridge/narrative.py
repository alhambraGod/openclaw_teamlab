"""
OpenClaw TeamLab — Student Growth Narrative Generator
Generates coherent longitudinal narratives of student development
by synthesizing CoEvo meeting reports, teacher feedback, and capability changes.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import openai
from sqlalchemy import text

from config.settings import settings
from config.database import get_db
from config.coevo_db import get_coevo_db

logger = logging.getLogger("teamlab.narrative")


def _get_llm_client() -> openai.OpenAI:
    return openai.OpenAI(
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY or "unused",
    )


async def _get_student_info(coevo_user_id: int) -> dict:
    """Fetch basic student info from CoEvo."""
    async with get_coevo_db() as session:
        result = await session.execute(
            text("SELECT id, username, email, role FROM users WHERE id = :uid"),
            {"uid": coevo_user_id},
        )
        row = result.mappings().first()
        if not row:
            return {"id": coevo_user_id, "username": "Unknown"}
        return dict(row)


async def _get_reports_for_period(coevo_user_id: int, months: int) -> list[dict]:
    """Get all meeting reports for a student over N months."""
    async with get_coevo_db() as session:
        result = await session.execute(
            text("""
                SELECT mr.phase, mr.task_items, mr.key_blockers,
                       mr.next_week_plan, mr.remarks,
                       mr.dialogue_detail, mr.core_viewpoints,
                       mr.teacher_suggestions, mr.teacher_comments,
                       mr.student_summary, mr.submitted_at,
                       m.meeting_name, m.meeting_time
                FROM meeting_reports mr
                JOIN meetings m ON mr.meeting_id = m.id
                WHERE mr.user_id = :uid
                  AND m.meeting_time >= DATE_SUB(NOW(), INTERVAL :months MONTH)
                ORDER BY m.meeting_time ASC
            """),
            {"uid": coevo_user_id, "months": months},
        )
        rows = result.mappings().all()
        return [dict(r) for r in rows]


async def _get_project_info(coevo_user_id: int) -> list[dict]:
    """Get the student's project memberships."""
    async with get_coevo_db() as session:
        result = await session.execute(
            text("""
                SELECT p.project_name, pm.project_role, pm.quarterly_goal, pm.short_term_goal
                FROM project_members pm
                JOIN projects p ON pm.project_id = p.id
                WHERE pm.user_id = :uid AND p.is_active = 1
            """),
            {"uid": coevo_user_id},
        )
        return [dict(r) for r in result.mappings().all()]


async def generate_student_narrative(coevo_user_id: int, months: int = 3) -> dict:
    """Generate a growth narrative for a student over the given period.

    Returns dict with: coevo_user_id, student_name, months_covered,
        narrative, key_milestones, current_assessment, recommendations
    """
    student = await _get_student_info(coevo_user_id)
    reports = await _get_reports_for_period(coevo_user_id, months)
    projects = await _get_project_info(coevo_user_id)

    student_name = student.get("username", "Unknown")

    if not reports:
        return {
            "coevo_user_id": coevo_user_id,
            "student_name": student_name,
            "months_covered": months,
            "narrative": f"{student_name}在过去{months}个月内暂无会议报告数据。",
            "key_milestones": [],
            "current_assessment": "数据不足",
            "recommendations": [],
        }

    # Build chronological summary for LLM
    report_summaries = []
    for r in reports:
        date_str = str(r.get("meeting_time", ""))[:10]
        meeting = r.get("meeting_name", "组会")
        parts = [f"[{date_str}] {meeting} ({r.get('phase', '?')})"]

        if r.get("task_items"):
            parts.append(f"  完成的任务: {r['task_items'][:200]}")
        if r.get("key_blockers"):
            parts.append(f"  阻塞问题: {r['key_blockers'][:200]}")
        if r.get("next_week_plan"):
            parts.append(f"  下周计划: {r['next_week_plan'][:200]}")
        if r.get("teacher_comments"):
            parts.append(f"  导师评语: {r['teacher_comments'][:200]}")
        if r.get("student_summary"):
            parts.append(f"  学生总结: {r['student_summary'][:200]}")

        report_summaries.append("\n".join(parts))

    project_info = ""
    if projects:
        project_info = "项目参与：" + "、".join(
            f"{p['project_name']}({p.get('project_role', '')})" for p in projects
        )

    prompt = f"""你是一位资深科研导师。请根据以下学生在过去{months}个月的会议报告数据，
生成一份结构化的成长叙事报告。

学生：{student_name}
{project_info}

按时间排列的会议报告：
{"---".join(report_summaries)}

请严格用以下JSON格式回复（不要输出任何其他内容）：
{{
  "narrative": "完整的成长叙事（按阶段描述发展历程，包含具体事件和转折点，500-800字）",
  "key_milestones": ["里程碑1", "里程碑2", ...],
  "current_assessment": "对当前状态的总体评估（100-200字）",
  "recommendations": ["建议1", "建议2", ...]
}}"""

    try:
        client = _get_llm_client()
        resp = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=4000,
        )
        content = resp.choices[0].message.content.strip()
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            data = json.loads(match.group())
        else:
            data = {"narrative": content, "key_milestones": [], "current_assessment": "", "recommendations": []}
    except Exception as e:
        logger.error("[Narrative] LLM call failed: %s", e)
        data = {
            "narrative": f"生成叙事失败: {e}",
            "key_milestones": [],
            "current_assessment": "生成失败",
            "recommendations": [],
        }

    result = {
        "coevo_user_id": coevo_user_id,
        "student_name": student_name,
        "months_covered": months,
        "narrative": data.get("narrative", ""),
        "key_milestones": data.get("key_milestones", []),
        "current_assessment": data.get("current_assessment", ""),
        "recommendations": data.get("recommendations", []),
    }

    # Cache to DB
    try:
        async with get_db() as session:
            await session.execute(
                text("""
                    INSERT INTO claw_student_narratives
                        (coevo_user_id, student_name, months_covered,
                         narrative_text, key_milestones, current_assessment,
                         recommendations, generated_at)
                    VALUES
                        (:uid, :name, :months, :narrative,
                         :milestones, :assessment, :recs, NOW())
                """),
                {
                    "uid": coevo_user_id,
                    "name": student_name,
                    "months": months,
                    "narrative": result["narrative"],
                    "milestones": json.dumps(result["key_milestones"], ensure_ascii=False),
                    "assessment": result["current_assessment"],
                    "recs": json.dumps(result["recommendations"], ensure_ascii=False),
                },
            )
    except Exception as e:
        logger.warning("[Narrative] Failed to cache narrative: %s", e)

    return result
