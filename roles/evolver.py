"""
OpenClaw TeamLab — Evolver Role（系统进化者）

职责：
  1. 分析近期任务执行数据（成功率、耗时、工具使用）
  2. 从 cognalign_coevo_prod 采集团队真实活跃度（会议频率、报告提交率、研究规划进展等）
  3. 将系统性能 + 团队活跃度合并分析，识别真实瓶颈和改进机会
  4. 生成系统健康报告和进化建议（双维度：AI 系统 + 团队现状）
  5. 可自动调整调度器频率（基于使用模式）
  6. 将进化洞见持久化，供 PI 查看和手动确认

这是系统"自主进化"能力的核心：进化分析必须基于 coevo 的最新事实，而非仅靠 AI 对话日志。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from config.database import get_db
from roles.base import AutonomousRole

logger = logging.getLogger("teamlab.roles.evolver")

EVOLVER_SYSTEM = """你是 OpenClaw TeamLab 系统的自我进化顾问。
你的任务是分析系统运行数据，提出具体可行的改进建议。

分析维度：
1. 任务成功率：哪些技能失败率高？原因是什么？
2. 用户查询模式：哪些问题被频繁问到？有没有未覆盖的需求？
3. 性能瓶颈：哪些任务耗时过长？
4. 知识缺口：系统对哪些方面理解不足？
5. 自动化建议：有哪些重复性工作可以自动化？

请给出2-5条具体的改进建议，每条包含：建议标题、详细描述、优先级（高/中/低）、实施难度（简单/中等/复杂）。
以 JSON 数组格式返回。"""


class Evolver(AutonomousRole):
    """系统进化者：分析性能数据，发现改进机会，推动系统自主进化。"""

    name = "evolver"
    description = "分析系统运行数据，识别改进点，生成进化建议，推动自主迭代升级"

    async def run(self) -> dict[str, Any]:
        logger.info("[Evolver] Starting system evolution analysis")
        start = datetime.now(timezone.utc)

        # 1. 收集系统运行数据
        stats = await self._collect_stats()
        query_patterns = await self._analyze_query_patterns()
        skill_performance = await self._analyze_skill_performance()

        # 2. 从 cognalign_coevo_prod 采集团队活跃度数据（进化分析的事实基础）
        team_activity = await self._collect_coevo_team_activity()
        if team_activity:
            logger.info("[Evolver] coevo team activity: meetings=%d report_rate=%.1f%%",
                        team_activity.get("meetings_total", 0),
                        team_activity.get("report_submission_rate", 0))

        # 3. LLM 分析并生成建议（综合系统数据 + 团队真实活跃度）
        suggestions = await self._generate_suggestions(stats, query_patterns, skill_performance, team_activity)

        # 4. 生成健康报告
        report = await self._generate_health_report(stats, suggestions, team_activity)

        # 4. 持久化
        if report:
            await self.save_insight(
                insight_type="system_evolution",
                subject=f"系统进化报告 {start.strftime('%Y-%m-%d')}",
                content=report,
                metadata={
                    "role": "evolver",
                    "stats": stats,
                    "suggestions_count": len(suggestions),
                    "run_at": start.isoformat(),
                },
            )

        for s in suggestions:
            await self.save_insight(
                insight_type="evolution_suggestion",
                subject=s.get("title", "改进建议"),
                content=s.get("description", ""),
                metadata={
                    "priority": s.get("priority", "中"),
                    "difficulty": s.get("difficulty", "中等"),
                    "source": "evolver",
                    "run_at": start.isoformat(),
                },
            )

        duration_s = (datetime.now(timezone.utc) - start).total_seconds()
        logger.info(
            "[Evolver] Generated %d suggestions in %.1fs",
            len(suggestions), duration_s,
        )

        return {
            "status": "completed",
            "stats": stats,
            "team_activity": team_activity,
            "suggestions": len(suggestions),
            "duration_seconds": round(duration_s, 1),
        }

    async def _collect_coevo_team_activity(self) -> dict[str, Any]:
        """
        从 cognalign_coevo_prod 采集近7天团队真实活跃度指标。
        这是进化分析的事实基础——进化不能脱离真实团队状态。
        """
        try:
            from data_bridge.coevo_knowledge_sync import CoevoKnowledgeSync
            return await CoevoKnowledgeSync.get_team_activity_stats(days=7)
        except Exception as exc:
            logger.warning("[Evolver] coevo team activity collection failed: %s", exc)
            return {}

    async def _collect_stats(self) -> dict[str, Any]:
        """收集过去7天的系统运行统计。"""
        try:
            async with get_db() as db:
                row = (await db.execute(
                    text("""
                        SELECT
                            COUNT(*)                                             AS total_tasks,
                            SUM(status = 'completed')                           AS completed,
                            SUM(status = 'failed')                              AS failed,
                            AVG(CASE WHEN status='completed' THEN duration_ms END) AS avg_ms,
                            MAX(CASE WHEN status='completed' THEN duration_ms END) AS max_ms,
                            COUNT(DISTINCT user_id)                             AS unique_users,
                            COUNT(DISTINCT skill_used)                          AS skills_used
                        FROM claw_task_log
                        WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
                          AND source != 'scheduler'
                    """)
                )).mappings().first()
                if row:
                    d = dict(row)
                    total = d.get("total_tasks") or 0
                    failed = d.get("failed") or 0
                    return {
                        "total_tasks": int(total),
                        "success_rate": round((1 - failed / max(1, total)) * 100, 1),
                        "failed_tasks": int(failed),
                        "avg_duration_ms": int(d.get("avg_ms") or 0),
                        "max_duration_ms": int(d.get("max_ms") or 0),
                        "unique_users": int(d.get("unique_users") or 0),
                        "skills_used": int(d.get("skills_used") or 0),
                    }
        except Exception as exc:
            logger.warning("[Evolver] Stats collection failed: %s", exc)
        return {}

    async def _analyze_query_patterns(self) -> list[dict]:
        """分析最频繁的查询模式。"""
        try:
            async with get_db() as db:
                rows = (await db.execute(
                    text("""
                        SELECT skill_used, COUNT(*) AS cnt,
                               AVG(duration_ms) AS avg_ms,
                               SUM(status='failed') AS fails
                        FROM claw_task_log
                        WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
                          AND source != 'scheduler'
                        GROUP BY skill_used
                        ORDER BY cnt DESC
                        LIMIT 10
                    """)
                )).mappings().all()
                return [dict(r) for r in rows]
        except Exception:
            return []

    async def _analyze_skill_performance(self) -> list[dict]:
        """识别性能异常的技能（高失败率 / 高耗时）。"""
        try:
            async with get_db() as db:
                rows = (await db.execute(
                    text("""
                        SELECT skill_used,
                               COUNT(*) AS total,
                               SUM(status='failed') AS fails,
                               AVG(duration_ms) AS avg_ms,
                               MAX(duration_ms) AS max_ms
                        FROM claw_task_log
                        WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
                        GROUP BY skill_used
                        HAVING total >= 3
                        ORDER BY (fails / total) DESC
                        LIMIT 10
                    """)
                )).mappings().all()
                return [dict(r) for r in rows]
        except Exception:
            return []

    async def _generate_suggestions(
        self,
        stats: dict,
        patterns: list[dict],
        performance: list[dict],
        team_activity: dict | None = None,
    ) -> list[dict]:
        """使用 LLM 生成系统改进建议（综合 AI 系统性能 + 真实团队活跃度）。"""
        if not stats:
            return []

        team_section = ""
        if team_activity:
            team_section = f"""
## 团队真实活跃度（来自 cognalign_coevo_prod）
{json.dumps(team_activity, ensure_ascii=False, indent=2)}

重要提示：
- report_submission_rate 低于 70% 说明成员参与度不足
- meetings_completed 远低于 meetings_total 说明会议执行有问题
- new_agent_memories 为 0 说明 CAMA 模块未正常运转
"""

        prompt = f"""以下是 OpenClaw TeamLab PI管理系统近7天的运行数据，以及真实团队活跃度数据：

## AI 系统整体统计
{json.dumps(stats, ensure_ascii=False, indent=2)}

## 技能使用频率（Top 10）
{json.dumps(patterns, ensure_ascii=False, indent=2)}

## 技能性能（按失败率排序）
{json.dumps(performance, ensure_ascii=False, indent=2)}
{team_section}
请综合以上数据，给出2-5条具体的改进建议。
重点关注：
1. 失败率高的技能及其根因
2. 高频使用场景是否得到充分支持
3. 团队活跃度指标是否正常，是否有值得 PI 关注的异常
4. AI 分析质量与团队真实状态的匹配度"""

        try:
            raw = await self.llm_call(
                prompt=prompt,
                system=EVOLVER_SYSTEM,
                max_tokens=1500,
                temperature=0.4,
            )
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1].lstrip("json").strip()
            suggestions = json.loads(raw)
            if isinstance(suggestions, list):
                return suggestions[:5]
        except Exception as exc:
            logger.warning("[Evolver] LLM suggestion generation failed: %s", exc)

        return []

    async def _generate_health_report(
        self,
        stats: dict,
        suggestions: list[dict],
        team_activity: dict | None = None,
    ) -> str:
        """生成双维度系统健康报告（AI 系统性能 + 团队真实活跃度）。"""
        if not stats:
            return "系统数据不足，无法生成报告。"

        total = stats.get("total_tasks", 0)
        success_rate = stats.get("success_rate", 0)
        avg_ms = stats.get("avg_duration_ms", 0)
        users = stats.get("unique_users", 0)

        health_emoji = "🟢" if success_rate >= 90 else "🟡" if success_rate >= 70 else "🔴"

        report_lines = [
            f"# {health_emoji} OpenClaw TeamLab 系统健康报告",
            f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "## AI 系统运行概览（近7天）",
            f"- 处理任务：{total} 条",
            f"- 成功率：{success_rate}%",
            f"- 平均耗时：{avg_ms/1000:.1f}s",
            f"- 活跃用户：{users} 人",
            "",
        ]

        # 团队活跃度部分（来自 cognalign_coevo_prod 真实数据）
        if team_activity:
            meetings_total = team_activity.get("meetings_total", 0)
            meetings_completed = team_activity.get("meetings_completed", 0)
            report_rate = team_activity.get("report_submission_rate", 0)
            plans = team_activity.get("research_plans_completed", 0)
            collabs = team_activity.get("collabs_completed", 0)
            memories = team_activity.get("new_agent_memories", 0)

            team_emoji = "🟢" if report_rate >= 80 else "🟡" if report_rate >= 50 else "🔴"
            report_lines.extend([
                f"## {team_emoji} 团队活跃度概览（近7天，来源：CoEvo）",
                f"- 会议：{meetings_completed}/{meetings_total} 已完成",
                f"- 报告提交率：{report_rate}%",
                f"- 研究规划完成：{plans} 份",
                f"- 协作推荐完成：{collabs} 份",
                f"- 新增 AI 记忆：{memories} 条",
                "",
            ])

            # 异常提示
            alerts = []
            if meetings_total > 0 and meetings_completed / max(1, meetings_total) < 0.5:
                alerts.append("⚠️ 会议完成率不足50%，建议 PI 关注会议执行情况")
            if report_rate < 60:
                alerts.append("⚠️ 报告提交率较低，部分成员可能未按时完成汇报")
            if memories == 0 and meetings_total > 0:
                alerts.append("⚠️ CAMA 模块本周未产生新记忆，请检查 CoEvo 系统运行状态")
            if alerts:
                report_lines.append("## 异常提示")
                for a in alerts:
                    report_lines.append(a)
                report_lines.append("")

        if suggestions:
            report_lines.append("## 进化建议")
            for i, s in enumerate(suggestions, 1):
                priority = s.get("priority", "中")
                difficulty = s.get("difficulty", "中等")
                report_lines.append(
                    f"{i}. **{s.get('title', '建议')}** "
                    f"（优先级: {priority} | 难度: {difficulty}）"
                )
                desc = s.get("description", "")
                if desc:
                    report_lines.append(f"   {desc[:200]}")
                report_lines.append("")

        report_lines.extend([
            "---",
            "*由 Evolver 自主角色生成，综合 AI 系统运行数据与 cognalign_coevo_prod 团队真实活跃度*",
        ])

        return "\n".join(report_lines)
