"""
OpenClaw TeamLab — Research Direction Analyzer

Reads CoEvo meeting data (reports, research plans, agent memories) and uses LLM
to cluster them into ResearchDirectionCluster + ResearchDirectionIdea records.

Called weekly by the scheduler (every Monday ~2am).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select, text as sa_text

from config.coevo_db import get_coevo_db
from config.database import get_db
from models import ResearchDirectionCluster, ResearchDirectionIdea
from models.coevo import (
    CoevoUser, CoevoProject, CoevoProjectMember,
    CoevoMeeting, CoevoMeetingReport, CoevoResearchPlan, CoevoAgentMemory,
)
from data_bridge import queries as Q

logger = logging.getLogger("teamlab.data_bridge.research_direction_analyzer")


async def analyze_research_directions() -> dict:
    """
    Main entry point. Collects CoEvo data, calls LLM, writes clusters.
    Returns summary stats.
    """
    logger.info("Research direction analysis started")

    # 1. Collect raw data
    context = await _collect_context()
    logger.info(
        "Collected context: %d projects, %d members, %d pre-reports, %d research-plans",
        len(context["projects"]),
        len(context["members"]),
        len(context["pre_reports"]),
        len(context["research_plans"]),
    )

    if not context["projects"]:
        logger.warning("No projects found — skipping analysis")
        return {"status": "skipped", "reason": "no_projects"}

    # 2. Build LLM prompt
    prompt = _build_analysis_prompt(context)

    # 3. Call LLM
    llm_output = await _call_llm(prompt)
    if not llm_output:
        return {"status": "failed", "reason": "llm_empty_response"}

    # 4. Parse and write to DB
    stats = await _write_clusters(llm_output, context)
    logger.info("Research direction analysis complete: %s", stats)
    return {"status": "completed", **stats}


async def _collect_context() -> dict:
    """Gather all relevant data from CoEvo for LLM analysis."""
    ctx: dict[str, Any] = {
        "projects": [],
        "members": [],
        "pre_reports": [],
        "research_plans": [],
        "agent_memories": [],
    }

    async with get_coevo_db() as db:
        # Projects
        projects = (await db.execute(
            select(CoevoProject).where(CoevoProject.is_active == True)
        )).scalars().all()
        ctx["projects"] = [{"id": p.id, "name": p.project_name} for p in projects]

        # Members (student level — username, bio, goals)
        members = (await db.execute(
            select(CoevoUser, CoevoProjectMember)
            .join(CoevoProjectMember, CoevoProjectMember.user_id == CoevoUser.id)
            .where(CoevoUser.is_active == True, CoevoUser.role == "student")
        )).all()
        for user, member in members:
            ctx["members"].append({
                "user_id": user.id,
                "username": user.username,
                "bio": (user.bio or "")[:300],
                "project_id": member.project_id,
                "quarterly_goal": (member.quarterly_goal or "")[:200],
                "short_term_goal": (member.short_term_goal or "")[:150],
            })

        # Recent pre-meeting reports (last 30)
        pre_rows = (await db.execute(
            select(CoevoMeetingReport, CoevoUser, CoevoMeeting)
            .join(CoevoUser, CoevoMeetingReport.user_id == CoevoUser.id)
            .join(CoevoMeeting, CoevoMeetingReport.meeting_id == CoevoMeeting.id)
            .where(
                CoevoMeetingReport.phase == "pre",
                CoevoMeetingReport.status == "submitted",
            )
            .order_by(CoevoMeeting.meeting_time.desc())
            .limit(30)
        )).all()
        for report, user, meeting in pre_rows:
            ctx["pre_reports"].append({
                "user": user.username,
                "meeting": meeting.meeting_name,
                "blockers": (report.key_blockers or "")[:200],
                "tasks": (report.task_items or "")[:200],
            })

        # Research plans
        plans = (await db.execute(
            select(CoevoResearchPlan)
            .where(CoevoResearchPlan.status.in_(["active", "completed"]))
            .order_by(CoevoResearchPlan.created_at.desc())
            .limit(20)
        )).scalars().all()
        for plan in plans:
            ctx["research_plans"].append({
                "title": (plan.title or "")[:150],
                "description": (plan.description or "")[:300],
                "status": plan.status,
            })

        # Agent memories (research context snippets)
        memories = (await db.execute(
            select(CoevoAgentMemory)
            .order_by(CoevoAgentMemory.created_at.desc())
            .limit(20)
        )).scalars().all()
        for mem in memories:
            ctx["agent_memories"].append({
                "content": (mem.content or "")[:300],
            })

    return ctx


def _build_analysis_prompt(ctx: dict) -> str:
    lines = [
        "你是一个科研团队方向分析助手。请根据以下团队数据，归纳并聚类该团队的主要研究方向。\n",
        "## 项目列表",
    ]
    for p in ctx["projects"]:
        lines.append(f"- [{p['id']}] {p['name']}")

    lines.append("\n## 成员目标摘要（按项目）")
    by_project: dict[int, list] = {}
    for m in ctx["members"]:
        by_project.setdefault(m["project_id"], []).append(m)
    for pid, mems in by_project.items():
        proj_name = next((p["name"] for p in ctx["projects"] if p["id"] == pid), f"项目{pid}")
        lines.append(f"\n### {proj_name}")
        for m in mems[:6]:  # limit per project
            if m["quarterly_goal"] or m["bio"]:
                lines.append(f"- {m['username']}: {m['quarterly_goal'] or m['bio'][:100]}")

    if ctx["research_plans"]:
        lines.append("\n## 研究计划")
        for plan in ctx["research_plans"][:10]:
            lines.append(f"- {plan['title']}: {plan['description'][:150]}")

    if ctx["pre_reports"]:
        lines.append("\n## 近期会议中的核心任务与阻碍（抽样）")
        for r in ctx["pre_reports"][:15]:
            if r["tasks"] or r["blockers"]:
                lines.append(f"- {r['user']} @ {r['meeting']}: 任务={r['tasks'][:100]} 阻碍={r['blockers'][:100]}")

    lines.append("""
## 输出要求

请以 JSON 格式输出，结构如下（只输出 JSON，不要有其他文字）：

```json
{
  "clusters": [
    {
      "topic": "研究方向主题名称（中文，简洁）",
      "description": "该方向的详细描述（2-4句）",
      "keywords": ["关键词1", "关键词2", "关键词3"],
      "similarity_group": "大类分组标签（如：NLP/CV/强化学习/系统优化/教育技术等）",
      "related_project_ids": [1, 2],
      "related_student_usernames": ["张三", "李四"],
      "confidence": 0.85,
      "source_evidence": "引用的具体会议/目标/计划中的关键句"
    }
  ],
  "ideas": [
    {
      "title": "潜在研究方向 idea 标题",
      "description": "为什么建议探索这个方向，与现有方向的关系",
      "inspiration_source": "gap_analysis",
      "related_cluster_topic": "对应上面 cluster 的 topic（可选）",
      "keywords": ["关键词1", "关键词2"]
    }
  ]
}
```

- clusters 应为 3-8 个，代表团队的主要研究方向
- ideas 应为 1-4 个，代表从现有数据中发现的潜在或未充分探索的方向
- 所有文本用中文
""")

    return "\n".join(lines)


async def _call_llm(prompt: str) -> dict | None:
    """Call Claude to analyze research directions. Returns parsed JSON or None."""
    try:
        import anthropic
        from config.settings import settings

        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        message = await client.messages.create(
            model="claude-opus-4-6",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = message.content[0].text if message.content else ""

        # Extract JSON from code block if present
        import re
        json_match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw_text)
        if json_match:
            raw_text = json_match.group(1)

        return json.loads(raw_text)

    except json.JSONDecodeError as e:
        logger.error("LLM returned invalid JSON: %s", e)
        return None
    except Exception as e:
        logger.error("LLM call failed: %s", e, exc_info=True)
        return None


async def _write_clusters(llm_output: dict, ctx: dict) -> dict:
    """
    Write LLM-generated clusters and ideas to the openclaw DB.
    Deactivates previous clusters before writing new ones.
    """
    clusters_data = llm_output.get("clusters", [])
    ideas_data = llm_output.get("ideas", [])

    # Build project lookup: name→id and id→name
    project_by_id = {p["id"]: p["name"] for p in ctx["projects"]}
    member_by_username = {m["username"]: m for m in ctx["members"]}

    clusters_created = 0
    ideas_created = 0

    async with get_db() as db:
        # Deactivate old clusters
        await db.execute(
            sa_text("UPDATE claw_research_direction_clusters SET is_active = 0 WHERE is_active = 1")
        )
        await db.commit()

        # Write new clusters
        cluster_topic_to_id: dict[str, int] = {}
        for c in clusters_data:
            related_projects = [
                {"id": pid, "name": project_by_id.get(pid, f"项目{pid}")}
                for pid in (c.get("related_project_ids") or [])
                if pid in project_by_id
            ]
            related_students = []
            for uname in (c.get("related_student_usernames") or []):
                m = member_by_username.get(uname)
                if m:
                    related_students.append({
                        "user_id": m["user_id"],
                        "username": uname,
                        "project_id": m["project_id"],
                    })

            cluster = ResearchDirectionCluster(
                topic=c.get("topic", "未命名方向")[:300],
                description=c.get("description", ""),
                keywords=c.get("keywords", []),
                similarity_group=c.get("similarity_group", "其他")[:100],
                related_projects=related_projects,
                related_students=related_students,
                confidence=float(c.get("confidence", 0.75)),
                source_evidence=c.get("source_evidence", ""),
                is_active=True,
                generated_at=datetime.utcnow(),
            )
            db.add(cluster)
            await db.flush()  # get cluster.id
            cluster_topic_to_id[c.get("topic", "")] = cluster.id
            clusters_created += 1

        await db.flush()

        # Write ideas
        for idea in ideas_data:
            related_topic = idea.get("related_cluster_topic", "")
            related_cluster_id = cluster_topic_to_id.get(related_topic)

            idea_obj = ResearchDirectionIdea(
                title=idea.get("title", "")[:300],
                description=idea.get("description", ""),
                inspiration_source=idea.get("inspiration_source", "gap_analysis")[:200],
                related_cluster_id=related_cluster_id,
                international_refs=[],
                status="pending",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(idea_obj)
            ideas_created += 1

        await db.commit()

    return {
        "clusters_created": clusters_created,
        "ideas_created": ideas_created,
    }
