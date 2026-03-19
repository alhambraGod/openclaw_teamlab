"""
Agent API — OpenClaw Agent 通过 bash+curl 调用的 HTTP 端点。
覆盖 PI 管理所需的查询、分析、写入能力，供 claw-openclaw 直接调用。
"""
import asyncio
import logging
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

# 直接 HTTP 调用的单个接口超时（非 Worker 异步队列路径）
AGENT_API_TIMEOUT_SECONDS = 30

logger = logging.getLogger("teamlab.agent_api")
router = APIRouter(prefix="/agent", tags=["agent"])


async def _invoke(module: str, func: str, timeout: int = AGENT_API_TIMEOUT_SECONDS, **kwargs) -> str:
    """
    调用 agent_actions 中的实现，带硬超时保护。
    超时后返回 [TIMEOUT] 前缀字符串，上层可将其转为 HTTP 408。
    """
    try:
        from agent_actions import (
            get_team_overview,
            get_person_context,
            execute_coevo_query,
            get_meeting_details,
            get_team_analytics,
            list_all_members,
            compute_student_risk,
            generate_growth_narrative,
            compute_collaboration_score,
            find_best_collaborators,
            get_action_items,
            log_insight,
            save_collaboration_recommendation,
        )
        fns = {
            "get_team_overview": get_team_overview,
            "get_person_context": get_person_context,
            "execute_coevo_query": execute_coevo_query,
            "get_meeting_details": get_meeting_details,
            "get_team_analytics": get_team_analytics,
            "list_all_members": list_all_members,
            "compute_student_risk": compute_student_risk,
            "generate_growth_narrative": generate_growth_narrative,
            "compute_collaboration_score": compute_collaboration_score,
            "find_best_collaborators": find_best_collaborators,
            "get_action_items": get_action_items,
            "log_insight": log_insight,
            "save_collaboration_recommendation": save_collaboration_recommendation,
        }
        fn = fns.get(func)
        if not fn:
            return f"[ERROR] Unknown action: {func}"
        return await asyncio.wait_for(fn(**kwargs), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("_invoke timeout: func=%s kwargs=%s", func, list(kwargs.keys()))
        return f"[TIMEOUT] 操作超时（>{timeout}s），请稍后重试或通过 POST /api/chat 提交异步任务。"
    except ImportError as e:
        logger.warning("agent_actions not available: %s", e)
        return f"[ERROR] Agent actions not loaded: {e}"
    except Exception as e:
        logger.exception("Agent action %s failed", func)
        return f"[ERROR] {e}"


def _raise_if_not_found(result: str, queried_name: str):
    """
    若 result 以 [NOT_FOUND] 开头，抛出 HTTP 404 并携带结构化候选信息。
    若 result 以 [TIMEOUT] 开头，抛出 HTTP 408 并携带友好提示。
    这样 OpenClaw agent 收到非 200 时能立即停止并回复用户，
    而不是继续调用其他接口做无效的搜索。
    """
    if not isinstance(result, str):
        return result

    if result.startswith("[NOT_FOUND]"):
        raise HTTPException(
            status_code=404,
            detail={
                "error": "member_not_found",
                "queried": queried_name,
                "message": result,
                "action": (
                    "请将上方 message 中的候选名单直接展示给用户，"
                    "询问他们要查询哪一位，收到确认后用正确名字重新发起请求。"
                    "不要自行猜测或继续调用其他分析接口。"
                ),
            },
        )

    if result.startswith("[TIMEOUT]"):
        raise HTTPException(
            status_code=408,
            detail={
                "error": "request_timeout",
                "queried": queried_name,
                "message": (
                    f"⏳ 查询「{queried_name}」耗时较长，已超出响应时限。\n\n"
                    "这通常意味着需要进行大量数据分析。建议：\n"
                    "1. 通过 POST /api/chat 提交为异步任务（会在后台完成后通知您）\n"
                    "2. 稍后重试（数据缓存后会快很多）"
                ),
                "action": "将上方 message 直接展示给用户，不要继续重试当前调用。",
            },
        )

    return result


# ── 查询类 ────────────────────────────────────────────────────────────────

@router.get("/team-overview")
async def api_team_overview():
    """获取团队全景快照。"""
    return await _invoke("query", "get_team_overview")


@router.get("/person-context")
async def api_person_context(name: str = Query(..., description="姓名或部分匹配")):
    """获取某人的完整上下文（项目、会议、blockers、风险等）。"""
    result = await _invoke("query", "get_person_context", name=name)
    return _raise_if_not_found(result, name)


@router.get("/meeting-details")
async def api_meeting_details(
    recent_n: int = Query(5, ge=1, le=20),
    project_name: str | None = Query(None),
    meeting_id: int | None = Query(None),
):
    """获取会议详情。"""
    return await _invoke(
        "query", "get_meeting_details",
        recent_n=recent_n, project_name=project_name, meeting_id=meeting_id,
    )


@router.get("/team-analytics")
async def api_team_analytics():
    """获取团队分析指标（创新指数、健康度、blockers 等）。"""
    return await _invoke("query", "get_team_analytics")


@router.get("/members")
async def api_members(role: str = Query("all", description="student|teacher|researcher|pm|all")):
    """列出团队成员。"""
    return await _invoke("query", "list_all_members", role=role)


@router.get("/search-member")
async def api_search_member(q: str = Query(..., description="搜索关键词（支持模糊匹配）")):
    """
    快速模糊搜索成员，返回候选列表。

    当用户输入的姓名可能有误时（如形近字、同音字），优先调用此接口
    获取候选列表并展示给用户确认，而不是直接调用耗时的分析接口。

    返回示例：
    {
      "query": "甄园谊",
      "found": false,
      "candidates": ["甄园昌", "甄园宜"],
      "message": "未找到"甄园谊"，您是否想查询：'甄园昌'、'甄园宜'？"
    }
    """
    try:
        from config.coevo_db import get_coevo_db
        from agent_actions._helpers import suggest_users
        from sqlalchemy import text

        async with get_coevo_db() as db:
            # 先尝试精确含字匹配
            r = await db.execute(
                text(
                    "SELECT username FROM users "
                    "WHERE username LIKE :n AND is_active=1 ORDER BY id LIMIT 5"
                ),
                {"n": f"%{q}%"},
            )
            exact_hits = [row[0] for row in r.fetchall()]

            if exact_hits:
                return {
                    "query": q,
                    "found": True,
                    "candidates": exact_hits,
                    "message": f"找到 {len(exact_hits)} 位匹配成员：{'、'.join(exact_hits)}",
                }

            # 无精确匹配 — 返回相似候选
            candidates = await suggest_users(q, db, limit=5)
            if candidates:
                candidate_str = "、".join(f"'{c}'" for c in candidates)
                return {
                    "query": q,
                    "found": False,
                    "candidates": candidates,
                    "message": f"未找到「{q}」，您是否想查询：{candidate_str}？请让用户确认后重试。",
                }
            else:
                return {
                    "query": q,
                    "found": False,
                    "candidates": [],
                    "message": f"未找到「{q}」，且无相似候选。请调用 GET /api/agent/members 查看完整成员列表。",
                }
    except Exception as e:
        logger.exception("search-member failed for q=%s", q)
        return {"query": q, "found": False, "candidates": [], "message": f"[ERROR] {e}"}


class QueryBody(BaseModel):
    sql: str


@router.post("/query")
async def api_query(body: QueryBody):
    """执行只读 SQL 查询（仅 SELECT/SHOW/DESCRIBE/EXPLAIN）。"""
    return await _invoke("query", "execute_coevo_query", sql=body.sql)


# ── 分析类 ────────────────────────────────────────────────────────────────

@router.get("/risk")
async def api_risk(student_name: str | None = Query(None)):
    """计算风险分（单人或不传则全员）。"""
    return await _invoke("analysis", "compute_student_risk", student_name=student_name)


@router.get("/growth-narrative")
async def api_growth_narrative(
    name: str = Query(...),
    months: int = Query(3, ge=1, le=12),
):
    """生成某人成长叙事（含 LLM，最长 60s）。"""
    result = await _invoke("analysis", "generate_growth_narrative", timeout=60, student_name=name, months=months)
    return _raise_if_not_found(result, name)


@router.get("/best-collaborators")
async def api_best_collaborators(
    name: str = Query(...),
    top: int = Query(5, ge=1, le=20),
):
    """为某人推荐最佳合作者。有缓存时 <1s；无缓存时触发 LLM 评估，最长 60s。"""
    result = await _invoke("analysis", "find_best_collaborators", timeout=60, person_name=name, top_k=top)
    return _raise_if_not_found(result, name)


@router.get("/collaboration-score")
async def api_collaboration_score(
    person_a: str = Query(...),
    person_b: str = Query(...),
):
    """分析两人合作价值。"""
    result = await _invoke("analysis", "compute_collaboration_score", person_a=person_a, person_b=person_b)
    # 两个参数都可能未找到，检查任意一个
    return _raise_if_not_found(result, f"{person_a}/{person_b}")


@router.get("/action-items")
async def api_action_items(status: str = Query("open,stale")):
    """获取待办事项。"""
    return await _invoke("analysis", "get_action_items", status_filter=status)


# ── 写入类 ────────────────────────────────────────────────────────────────

class LogInsightBody(BaseModel):
    insight_type: str = "other"
    content: str
    subject: str | None = None


@router.post("/log-insight")
async def api_log_insight(body: LogInsightBody):
    """持久化洞见。"""
    return await _invoke(
        "write", "log_insight",
        insight_type=body.insight_type,
        content=body.content,
        title=body.subject or body.content[:80],
    )


class SaveCollabBody(BaseModel):
    person_a: str
    person_b: str
    score: float
    reasoning: str
    ideas: list[str] = []


@router.post("/save-collaboration")
async def api_save_collaboration(body: SaveCollabBody):
    """保存协作推荐结果。"""
    return await _invoke(
        "write", "save_collaboration_recommendation",
        person_a_name=body.person_a, person_b_name=body.person_b,
        score=body.score, reasoning=body.reasoning, collaboration_ideas=body.ideas,
    )


# ── 全球研究热点 ─────────────────────────────────────────────────────────

@router.get("/global-research")
async def api_global_research(
    topic: str | None = Query(None, description="按主题关键词过滤，为空则返回全部"),
    days: int = Query(7, ge=1, le=90, description="查询最近N天的洞见"),
    top: int = Query(5, ge=1, le=20, description="返回条数"),
):
    """
    获取全球最新研究热点与团队机遇洞见。
    数据由每日调度任务自动从 arxiv / Semantic Scholar 抓取并 LLM 分析生成。
    """
    try:
        from data_bridge.global_research_monitor import get_latest_insights
        insights = await get_latest_insights(
            insight_type="global_research",
            topic=topic,
            days=days,
            limit=top,
        )
        if not insights:
            return "（暂无全球研究洞见，将由每日调度任务自动生成。如需立即触发，请调用 POST /api/agent/scan-global-research）"

        lines = [f"## 全球研究热点洞见（最近 {days} 天，共 {len(insights)} 条）\n"]
        for item in insights:
            lines.append(f"### {item['subject']} — {item['created_at'][:10]}")
            lines.append(item["content"])
            meta = item.get("metadata", {})
            if meta.get("paper_titles"):
                lines.append("\n**涉及论文**: " + " | ".join(meta["paper_titles"][:3]))
            lines.append("")
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("global-research failed")
        return f"[ERROR] {exc}"


@router.get("/cross-project")
async def api_cross_project(days: int = Query(14, ge=1, le=90)):
    """获取跨项目协作机会分析（由调度任务生成，或实时触发）。"""
    try:
        from data_bridge.global_research_monitor import get_latest_insights
        insights = await get_latest_insights(
            insight_type="cross_project",
            days=days,
            limit=1,
        )
        if insights:
            item = insights[0]
            return f"## 跨项目协作机会分析\n（更新于 {item['created_at'][:10]}）\n\n{item['content']}"

        # 无缓存 → 实时分析
        from data_bridge.global_research_monitor import analyze_cross_project_collaboration
        result = await analyze_cross_project_collaboration()
        return f"## 跨项目协作机会分析（实时）\n\n{result}"
    except Exception as exc:
        logger.exception("cross-project failed")
        return f"[ERROR] {exc}"


@router.get("/team-knowledge")
async def api_team_knowledge(
    subject: str | None = Query(None, description="按主体名过滤（人名/项目名）"),
    days: int = Query(30, ge=1, le=365),
):
    """检索知识管理者（Librarian）积累的团队知识片段。"""
    try:
        from sqlalchemy import text
        from config.database import get_db
        async with get_db() as db:
            q_filter = ""
            params: dict = {"days": days, "limit": 30}
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
                    ORDER BY created_at DESC LIMIT :limit
                """),
                params,
            )).mappings().all()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.exception("team-knowledge failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/evolution-report")
async def api_evolution_report():
    """获取系统进化者最新健康报告和改进建议。"""
    try:
        from sqlalchemy import text
        from config.database import get_db
        async with get_db() as db:
            report = (await db.execute(
                text("""
                    SELECT content, created_at FROM claw_pi_agent_insights
                    WHERE insight_type = 'system_evolution'
                      AND subject LIKE '%系统进化报告%'
                    ORDER BY created_at DESC LIMIT 1
                """)
            )).mappings().first()
            suggestions = (await db.execute(
                text("""
                    SELECT subject, content, metadata, created_at
                    FROM claw_pi_agent_insights
                    WHERE insight_type = 'evolution_suggestion'
                    ORDER BY created_at DESC LIMIT 10
                """)
            )).mappings().all()
            return {
                "report": dict(report) if report else None,
                "suggestions": [dict(r) for r in suggestions],
            }
    except Exception as exc:
        logger.exception("evolution-report failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/monitor")
async def api_monitor():
    """
    Heartbeat 专用轻量巡检接口 — 纯 DB 查询，无 LLM 调用，< 500ms。
    返回：红色风险成员、逾期行动项、今日全球研究摘要（如有）。
    供 OpenClaw Heartbeat 每 30 分钟静默扫描，有异常才推送飞书告警。
    """
    from sqlalchemy import text
    from config.database import get_db
    from config.coevo_db import get_coevo_db

    result: dict = {
        "red_risks": [],
        "stale_actions": [],
        "global_research_today": None,
        "checked_at": None,
    }

    import datetime
    result["checked_at"] = datetime.datetime.now().isoformat()

    # ── 1. 红色风险成员（读本地 insights 缓存，无 LLM）────────────────────────
    try:
        async with get_db() as db:
            rows = (await db.execute(text("""
                SELECT subject, content, created_at
                FROM claw_pi_agent_insights
                WHERE insight_type = 'risk_alert'
                  AND JSON_EXTRACT(metadata, '$.level') = 'red'
                  AND created_at >= DATE_SUB(NOW(), INTERVAL 2 DAY)
                ORDER BY created_at DESC
                LIMIT 10
            """))).mappings().all()
            result["red_risks"] = [
                {"name": r["subject"], "content": r["content"][:200], "at": str(r["created_at"])}
                for r in rows
            ]
    except Exception:
        pass

    # ── 2. 逾期行动项（>7天未更新的 open/stale 项）──────────────────────────
    try:
        async with get_coevo_db() as db:
            rows = (await db.execute(text("""
                SELECT title, owner, due_date, updated_at
                FROM action_items
                WHERE status IN ('open', 'in_progress')
                  AND (due_date < CURDATE() OR updated_at < DATE_SUB(NOW(), INTERVAL 7 DAY))
                ORDER BY due_date ASC
                LIMIT 10
            """))).mappings().all()
            result["stale_actions"] = [
                {"title": r["title"], "owner": r.get("owner", ""), "due": str(r.get("due_date", ""))}
                for r in rows
            ]
    except Exception:
        pass

    # ── 3. 今日全球研究摘要（读缓存，无实时抓取）──────────────────────────
    try:
        async with get_db() as db:
            row = (await db.execute(text("""
                SELECT subject, content, created_at
                FROM claw_pi_agent_insights
                WHERE insight_type = 'global_research'
                  AND DATE(created_at) = CURDATE()
                ORDER BY created_at DESC
                LIMIT 1
            """))).mappings().first()
            if row:
                result["global_research_today"] = {
                    "subject": row["subject"],
                    "summary": row["content"][:400],
                    "at": str(row["created_at"]),
                }
    except Exception:
        pass

    return result


@router.post("/scan-global-research")
async def api_scan_global_research(
    topic: str | None = Query(None, description="可选：指定单一主题，为空则扫描全部主题"),
):
    """
    手动触发全球研究扫描（正常由每日调度任务自动执行）。
    返回扫描摘要，实际扫描在后台异步进行。
    """
    import asyncio as _asyncio
    from data_bridge.global_research_monitor import (
        scan_global_research,
        TEAM_TOPIC_QUERIES,
    )

    topics = None
    if topic:
        topics = [t for t in TEAM_TOPIC_QUERIES if topic.lower() in t["topic"].lower() or topic in t["zh"]]
        if not topics:
            topics = [{"topic": topic, "arxiv_query": topic, "zh": topic}]

    # 后台任务，不等待完成，立即返回
    _asyncio.create_task(scan_global_research(topics=topics, with_cross_project=(topic is None)))
    return {
        "status": "scanning",
        "message": f"全球研究扫描已在后台启动，覆盖 {len(topics) if topics else len(TEAM_TOPIC_QUERIES)} 个主题。完成后可通过 GET /api/agent/global-research 查询结果。",
    }


# ── Email / 通知 ──────────────────────────────────────────────────────────────

class SendEmailRequest(BaseModel):
    to: str                          # 收件人邮箱
    subject: str = "OpenClaw TeamLab 通知"
    content: str                     # 邮件正文（Markdown 纯文本）
    html: bool = False               # True 时以 HTML 格式发送


@router.post("/send-email")
async def api_send_email(body: SendEmailRequest):
    """
    向指定邮箱发送邮件。
    - 仅在用户明确提供邮箱地址时调用
    - 发送前须在回复中告知用户「将发送至 xxx@yyy.com」
    - content 支持纯文本，支持 Markdown 格式（非 html 模式）
    """
    import re
    email_re = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    if not email_re.match(body.to.strip()):
        raise HTTPException(status_code=422, detail=f"无效邮箱地址：{body.to}")

    from notify.email import send_email
    ok = await send_email(
        to=body.to.strip(),
        subject=body.subject,
        body=body.content,
        html=body.html,
    )
    if ok:
        return {"status": "sent", "to": body.to, "subject": body.subject}
    raise HTTPException(
        status_code=502,
        detail={
            "error": "email_send_failed",
            "message": "邮件发送失败，请检查 SMTP 配置或稍后重试。",
            "to": body.to,
        },
    )
