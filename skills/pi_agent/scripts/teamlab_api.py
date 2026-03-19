"""
PI Agent — TeamLab Agent API 工具
供 Worker 中 pi_agent skill 调用，与 /api/agent/* 共享 agent_actions 实现。
"""
from __future__ import annotations

from typing import Optional

from agent_actions import (
    get_team_overview as _get_team_overview,
    get_person_context as _get_person_context,
    execute_coevo_query as _execute_coevo_query,
    get_meeting_details as _get_meeting_details,
    get_team_analytics as _get_team_analytics,
    list_all_members as _list_all_members,
    compute_student_risk as _compute_student_risk,
    generate_growth_narrative as _generate_growth_narrative,
    compute_collaboration_score as _compute_collaboration_score,
    find_best_collaborators as _find_best_collaborators,
    get_action_items as _get_action_items,
)


async def get_team_overview() -> str:
    """获取团队全景快照。"""
    return await _get_team_overview()


async def get_person_context(name: str) -> str:
    """获取某人的完整上下文（项目、会议、blockers、风险等）。name 支持部分匹配。"""
    return await _get_person_context(name=name)


async def get_best_collaborators(name: str, top: int = 5) -> str:
    """为某人推荐最佳合作者。"""
    return await _find_best_collaborators(person_name=name, top_k=top)


async def compute_collaboration_score(person_a: str, person_b: str) -> str:
    """分析两人合作价值。"""
    return await _compute_collaboration_score(person_a=person_a, person_b=person_b)


async def execute_coevo_query(sql: str) -> str:
    """执行只读 SQL 查询（SELECT/SHOW/DESCRIBE/EXPLAIN）。"""
    return await _execute_coevo_query(sql=sql)


async def get_meeting_details(recent_n: int = 5, project_name: Optional[str] = None) -> str:
    """获取会议详情。"""
    return await _get_meeting_details(recent_n=recent_n, project_name=project_name)


async def get_team_analytics() -> str:
    """获取团队分析指标（创新指数、健康度、blockers 等）。"""
    return await _get_team_analytics()


async def list_all_members(role: str = "all") -> str:
    """列出团队成员。role: student|teacher|researcher|pm|all"""
    return await _list_all_members(role=role)


async def compute_student_risk(student_name: Optional[str] = None) -> str:
    """计算风险分。不传 student_name 则全员。"""
    return await _compute_student_risk(student_name=student_name)


async def generate_growth_narrative(name: str, months: int = 3) -> str:
    """生成某人成长叙事。"""
    return await _generate_growth_narrative(student_name=name, months=months)


async def get_action_items(status: str = "open,stale") -> str:
    """获取待办事项。"""
    return await _get_action_items(status_filter=status)


async def get_global_research(topic: Optional[str] = None, days: int = 7, top: int = 5) -> str:
    """
    获取全球最新研究热点与团队机遇洞见。
    topic: 按关键词过滤（如"大模型对齐"），为空则返回全部。
    days: 最近N天，默认7天。
    """
    from data_bridge.global_research_monitor import get_latest_insights
    insights = await get_latest_insights(
        insight_type="global_research",
        topic=topic,
        days=days,
        limit=top,
    )
    if not insights:
        return "（暂无全球研究洞见数据，调度任务将在每天 06:00 自动抓取）"

    lines = [f"## 全球研究热点（最近 {days} 天）\n"]
    for item in insights:
        lines.append(f"### {item['subject']} — {item['created_at'][:10]}")
        lines.append(item["content"])
        meta = item.get("metadata", {})
        if meta.get("paper_titles"):
            lines.append("\n**涉及论文**: " + " | ".join(meta["paper_titles"][:3]))
        lines.append("")
    return "\n".join(lines)


async def get_cross_project_opportunities(days: int = 14) -> str:
    """
    获取跨项目协作机会分析：技术互补、数据共享、联合发表潜力。
    days: 最近N天，默认14天。
    """
    from data_bridge.global_research_monitor import get_latest_insights, analyze_cross_project_collaboration
    insights = await get_latest_insights(insight_type="cross_project", days=days, limit=1)
    if insights:
        item = insights[0]
        return f"## 跨项目协作机会\n（分析于 {item['created_at'][:10]}）\n\n{item['content']}"
    # 无缓存 → 实时分析
    return await analyze_cross_project_collaboration()


async def get_team_knowledge(subject: Optional[str] = None, days: int = 30) -> str:
    """
    检索知识管理者（Librarian）积累的团队知识片段。
    subject: 可选，按人名/项目名过滤（如"张三"、"项目A"）。
    days: 最近N天内的知识，默认30天。
    用途：回答关于团队成员特征、项目进展的具体问题时，先检索已积累的知识。
    """
    from sqlalchemy import text
    from config.database import get_db
    try:
        async with get_db() as db:
            q_filter = ""
            params: dict = {"days": days, "limit": 20}
            if subject:
                q_filter = "AND subject LIKE :subject"
                params["subject"] = f"%{subject}%"
            rows = (await db.execute(
                text(f"""
                    SELECT subject, content, created_at
                    FROM claw_pi_agent_insights
                    WHERE insight_type = 'team_knowledge'
                      AND created_at >= DATE_SUB(NOW(), INTERVAL :days DAY)
                      {q_filter}
                    ORDER BY created_at DESC
                    LIMIT :limit
                """),
                params,
            )).mappings().all()
            if not rows:
                return "暂无相关团队知识积累。"
            lines = [f"## 团队知识库（近{days}天）"]
            for r in rows:
                lines.append(f"- **{r['subject']}**（{str(r['created_at'])[:10]}）：{r['content']}")
            return "\n".join(lines)
    except Exception as exc:
        return f"检索团队知识失败: {exc}"


async def get_system_evolution_report() -> str:
    """
    获取系统进化者（Evolver）生成的最新健康报告和改进建议。
    用途：了解系统状态、识别改进机会、查看自动化建议。
    """
    from sqlalchemy import text
    from config.database import get_db
    try:
        async with get_db() as db:
            # 最新健康报告
            report_row = (await db.execute(
                text("""
                    SELECT content, created_at FROM claw_pi_agent_insights
                    WHERE insight_type = 'system_evolution'
                      AND subject LIKE '%系统进化报告%'
                    ORDER BY created_at DESC LIMIT 1
                """)
            )).mappings().first()

            # 最新改进建议（最近7天）
            suggestion_rows = (await db.execute(
                text("""
                    SELECT subject, content, metadata FROM claw_pi_agent_insights
                    WHERE insight_type = 'evolution_suggestion'
                      AND created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
                    ORDER BY created_at DESC LIMIT 5
                """)
            )).mappings().all()

            parts = []
            if report_row:
                parts.append(report_row["content"])
            if suggestion_rows:
                parts.append("\n## 近期改进建议")
                for s in suggestion_rows:
                    import json
                    meta = {}
                    if s["metadata"]:
                        try:
                            meta = json.loads(s["metadata"]) if isinstance(s["metadata"], str) else s["metadata"]
                        except Exception:
                            pass
                    priority = meta.get("priority", "中")
                    parts.append(f"- [{priority}] **{s['subject']}**: {s['content'][:150]}")
            return "\n".join(parts) if parts else "系统进化报告尚未生成，可在调度管理中手动触发 evolver 任务。"
    except Exception as exc:
        return f"获取进化报告失败: {exc}"
