"""
Agent Actions — 查询类（团队概览、成员详情、会议记录等）。
"""
import os
import re
import logging
from typing import Optional

from agent_actions._helpers import resolve_user

logger = logging.getLogger("agent_actions.query")

# 同进程调用时使用本地地址
TEAMLAB_BASE = os.environ.get("TEAMLAB_URL", "http://127.0.0.1:10301")


async def get_team_overview() -> str:
    try:
        from data_bridge.team_context import get_team_snapshot
        return await get_team_snapshot(force_refresh=True)
    except Exception as e:
        logger.error("get_team_overview failed: %s", e)
        return f"[ERROR] Could not retrieve team overview: {e}"


async def get_person_context(name: str) -> str:
    try:
        from sqlalchemy import text
        from config.coevo_db import get_coevo_db
        from config.database import get_db

        async with get_coevo_db() as db:
            user_match, note = await resolve_user(name, db)

        if not user_match:
            return note

        # 支持返回 1 条精确匹配的上下文（resolve_user 已选出最佳）
        users = [user_match]
        fuzzy_prefix = f"{note}\n" if note else ""

        results = []
        for user in users:
            uid = user["id"]
            uname = user["username"]
            lines = [fuzzy_prefix + f"## {uname} (id={uid}, role={user['role']})\n"]
            if user["bio"]:
                lines.append(f"**简介**: {user['bio']}\n")

            async with get_coevo_db() as db:
                r = await db.execute(text("""
                    SELECT p.project_name, p.id as pid, pm.project_role, pm.project_auth,
                           pm.quarterly_goal, pm.short_term_goal, pm.display_name
                    FROM project_members pm
                    JOIN projects p ON p.id = pm.project_id AND p.is_active=1
                    WHERE pm.user_id = :uid
                """), {"uid": uid})
                memberships = r.mappings().all()

            if memberships:
                lines.append("### 项目参与")
                for m in memberships:
                    lines.append(f"- **{m['project_name']}** (角色: {m['project_role'] or '成员'})")
                    if m["quarterly_goal"]:
                        lines.append(f"  季度目标: {m['quarterly_goal'][:300]}")
                    if m["short_term_goal"]:
                        lines.append(f"  近期目标: {m['short_term_goal'][:200]}")
                lines.append("")

            async with get_coevo_db() as db:
                r = await db.execute(text("""
                    SELECT mr.phase, mr.task_items, mr.key_blockers, mr.next_week_plan,
                           mr.student_summary, mr.teacher_suggestions, mr.teacher_comments,
                           mr.core_viewpoints, m.meeting_name, m.meeting_time
                    FROM meeting_reports mr
                    JOIN meetings m ON mr.meeting_id = m.id
                    WHERE mr.user_id = :uid
                    ORDER BY m.meeting_time DESC LIMIT 6
                """), {"uid": uid})
                reports = r.mappings().all()

            if reports:
                lines.append("### 近期会议报告")
                for rpt in reports:
                    date_str = str(rpt["meeting_time"] or "")[:10]
                    lines.append(f"\n**{rpt['meeting_name']}** ({date_str}, {rpt['phase']}期)")
                    if rpt["task_items"]:
                        lines.append(f"- 完成任务: {rpt['task_items'][:300]}")
                    if rpt["key_blockers"]:
                        lines.append(f"- **阻塞问题**: {rpt['key_blockers'][:300]}")
                    if rpt["next_week_plan"]:
                        lines.append(f"- 下周计划: {rpt['next_week_plan'][:200]}")
                    if rpt["teacher_comments"]:
                        lines.append(f"- 导师评语: {rpt['teacher_comments'][:200]}")
                    if rpt["student_summary"]:
                        lines.append(f"- 学生总结: {rpt['student_summary'][:200]}")
                lines.append("")

            async with get_coevo_db() as db:
                r = await db.execute(text("""
                    SELECT plan_name, final_goal, status, created_at
                    FROM research_plans
                    WHERE creator_user_id = :uid AND status IN ('active','completed')
                    ORDER BY created_at DESC LIMIT 5
                """), {"uid": uid})
                plans = r.mappings().all()

            if plans:
                lines.append("### 研究计划")
                for p in plans:
                    goal = (p["final_goal"] or p["plan_name"] or "")[:200]
                    lines.append(f"- [{p['status']}] {goal}")
                lines.append("")

            async with get_coevo_db() as db:
                r = await db.execute(text("""
                    SELECT target_user_ids, collaboration_suggestion, created_at, status
                    FROM collaboration_recommendations
                    WHERE requester_user_id = :uid
                    ORDER BY created_at DESC LIMIT 5
                """), {"uid": uid})
                collabs_as_req = r.mappings().all()
                r2 = await db.execute(text("""
                    SELECT cr.requester_user_id, cr.collaboration_suggestion, u.username as requester_name
                    FROM collaboration_recommendations cr
                    JOIN users u ON u.id = cr.requester_user_id
                    WHERE JSON_CONTAINS(cr.target_user_ids, CAST(:uid AS JSON)) AND cr.status='completed'
                    ORDER BY cr.created_at DESC LIMIT 5
                """), {"uid": uid})
                collabs_as_target = r2.mappings().all()

            if collabs_as_req or collabs_as_target:
                lines.append("### 协作推荐历史")
                if collabs_as_req:
                    lines.append("**以Ta为发起人**:")
                    for c in collabs_as_req:
                        suggestion = (c["collaboration_suggestion"] or "")[:200]
                        lines.append(f"- ({c['status']}) {suggestion or '推荐记录'}")
                if collabs_as_target:
                    lines.append("**被他人推荐合作**:")
                    for c in collabs_as_target:
                        suggestion = (c["collaboration_suggestion"] or "")[:150]
                        lines.append(f"- {c['requester_name']} 推荐与Ta合作: {suggestion}")
                lines.append("")

            async with get_coevo_db() as db:
                r = await db.execute(text("""
                    SELECT memory_type, content, created_at
                    FROM agent_memories
                    WHERE user_id = :uid
                    ORDER BY created_at DESC LIMIT 8
                """), {"uid": uid})
                memories = r.mappings().all()

            if memories:
                lines.append("### AI记忆条目 (CAMA)")
                for mem in memories:
                    date_str = str(mem["created_at"] or "")[:10]
                    content_snippet = (mem["content"] or "")[:200]
                    lines.append(f"- [{mem['memory_type']}] ({date_str}) {content_snippet}")
                lines.append("")

            try:
                async with get_db() as tdb:
                    r = await tdb.execute(text("""
                        SELECT overall_score, risk_level, explanation, computed_at
                        FROM claw_student_risk_scores
                        WHERE coevo_user_id = :uid
                        ORDER BY computed_at DESC LIMIT 1
                    """), {"uid": uid})
                    risk_row = r.mappings().first()
                if risk_row:
                    lines.append("### 风险评分 (TeamLab)")
                    lines.append(f"- 综合分: {risk_row['overall_score']} | 级别: {risk_row['risk_level']}")
                    lines.append(f"- {risk_row['explanation']}")
                    lines.append(f"- 计算时间: {str(risk_row['computed_at'])[:16]}")
            except Exception:
                pass

            results.append("\n".join(lines))

        return "\n\n---\n\n".join(results)

    except Exception as e:
        logger.error("get_person_context(%s) failed: %s", name, e, exc_info=True)
        return f"[ERROR] {e}"


async def execute_coevo_query(sql: str) -> str:
    sql_clean = sql.strip().lstrip(";").strip()
    first_word = sql_clean.split()[0].upper() if sql_clean else ""
    if first_word not in ("SELECT", "SHOW", "DESCRIBE", "EXPLAIN"):
        return "[BLOCKED] Only SELECT/SHOW/DESCRIBE/EXPLAIN queries allowed on CoEvo DB."

    try:
        from sqlalchemy import text
        from config.coevo_db import get_coevo_db

        # 若 SQL 已含 LIMIT，不再追加，避免 "LIMIT 5 LIMIT 100" 语法错误
        has_limit = bool(re.search(r'\bLIMIT\b', sql_clean, re.IGNORECASE))
        final_sql = sql_clean if has_limit else sql_clean.rstrip(";") + " LIMIT 100"

        async with get_coevo_db() as db:
            result = await db.execute(text(final_sql))
            rows = result.mappings().all()

        if not rows:
            return "(No results)"

        cols = list(rows[0].keys())
        lines = ["| " + " | ".join(cols) + " |"]
        lines.append("|" + "|".join(["---"] * len(cols)) + "|")
        for row in rows:
            cells = [str(row[c])[:100] if row[c] is not None else "NULL" for c in cols]
            lines.append("| " + " | ".join(cells) + " |")

        return f"**{len(rows)} rows**\n\n" + "\n".join(lines)

    except Exception as e:
        logger.error("execute_coevo_query failed: %s", e)
        return f"[ERROR] Query failed: {e}\nSQL: {sql_clean}"


async def get_meeting_details(
    meeting_id: Optional[int] = None,
    recent_n: int = 5,
    project_name: Optional[str] = None,
) -> str:
    try:
        from sqlalchemy import text
        from config.coevo_db import get_coevo_db

        async with get_coevo_db() as db:
            if meeting_id:
                r = await db.execute(text("SELECT * FROM meetings WHERE id = :mid LIMIT 1"), {"mid": meeting_id})
            elif project_name:
                r = await db.execute(text("""
                    SELECT m.* FROM meetings m
                    JOIN projects p ON m.project_id = p.id
                    WHERE p.project_name LIKE :pn
                    ORDER BY m.meeting_time DESC LIMIT :n
                """), {"pn": f"%{project_name}%", "n": recent_n})
            else:
                r = await db.execute(text("SELECT * FROM meetings ORDER BY meeting_time DESC LIMIT :n"), {"n": recent_n})
            meetings_list = r.mappings().all()

        if not meetings_list:
            return "(No meetings found)"

        all_lines = []
        for mtg in meetings_list:
            mid = mtg["id"]
            lines = [
                f"## 会议: {mtg.get('meeting_name', '?')} (id={mid})",
                f"- 时间: {str(mtg.get('meeting_time', ''))[:16]}",
                f"- 状态: {mtg.get('status', '?')} | 阶段: {mtg.get('phase', '?')}",
            ]
            async with get_coevo_db() as db:
                r = await db.execute(text("""
                    SELECT mr.user_id, mr.phase, mr.task_items, mr.key_blockers,
                           mr.next_week_plan, mr.student_summary,
                           mr.teacher_comments, mr.teacher_suggestions,
                           u.username
                    FROM meeting_reports mr
                    JOIN users u ON u.id = mr.user_id
                    WHERE mr.meeting_id = :mid
                    ORDER BY mr.phase, u.username
                """), {"mid": mid})
                reports = r.mappings().all()

            if reports:
                lines.append(f"\n### 报告 ({len(reports)} 份)")
                for rpt in reports:
                    lines.append(f"\n**{rpt['username']}** ({rpt['phase']}期):")
                    if rpt["task_items"]:
                        lines.append(f"  任务: {rpt['task_items'][:300]}")
                    if rpt["key_blockers"]:
                        lines.append(f"  **阻塞**: {rpt['key_blockers'][:300]}")
                    if rpt["next_week_plan"]:
                        lines.append(f"  计划: {rpt['next_week_plan'][:200]}")
                    if rpt["teacher_comments"]:
                        lines.append(f"  导师评语: {rpt['teacher_comments'][:200]}")

            all_lines.append("\n".join(lines))

        return "\n\n---\n\n".join(all_lines)

    except Exception as e:
        logger.error("get_meeting_details failed: %s", e)
        return f"[ERROR] {e}"


async def get_team_analytics() -> str:
    try:
        import httpx

        lines = ["## 团队分析报告\n"]
        async with httpx.AsyncClient(timeout=30) as client:
            for endpoint, title, key in [
                ("/api/coevo/innovation-index", "### 创新指数", "innovation_index"),
                ("/api/coevo/team-health", "### 团队健康度", "health_score"),
            ]:
                try:
                    r = await client.get(f"{TEAMLAB_BASE}{endpoint}")
                    if r.status_code == 200:
                        data = r.json()
                        lines.append(title)
                        lines.append(f"- 综合: {data.get(key, 'N/A')}")
                        for k, v in (data.get("components") or data.get("signals") or {}).items():
                            lines.append(f"  - {k}: {v}")
                        lines.append("")
                except Exception:
                    pass

            try:
                r = await client.get(f"{TEAMLAB_BASE}/api/coevo/analytics/blockers")
                if r.status_code == 200:
                    data = r.json()
                    lines.append("### 全团队阻塞问题分析")
                    for b in (data.get("blockers") or [])[:10]:
                        lines.append(f"- {b.get('pattern', b.get('description', '')[:100])}: 出现{b.get('count', '?')}次")
                    lines.append("")
            except Exception:
                pass

            try:
                r = await client.get(f"{TEAMLAB_BASE}/api/coevo/analytics/meeting-engagement")
                if r.status_code == 200:
                    data = r.json()
                    lines.append("### 会议参与度")
                    for item in (data.get("engagement") or [])[:10]:
                        name = item.get("username", item.get("student_name", "?"))
                        rate = item.get("engagement_rate", item.get("rate", "?"))
                        lines.append(f"- {name}: {rate}")
                    lines.append("")
            except Exception:
                pass

        if len(lines) == 1:
            return "(Analytics service unavailable — try execute_coevo_query for raw data)"
        return "\n".join(lines)

    except Exception as e:
        logger.error("get_team_analytics failed: %s", e)
        return f"[ERROR] {e}"


async def list_all_members(role: str = "all") -> str:
    try:
        from sqlalchemy import text
        from config.coevo_db import get_coevo_db

        async with get_coevo_db() as db:
            if role == "all":
                r = await db.execute(text("""
                    SELECT u.id, u.username, u.role, u.email,
                           GROUP_CONCAT(p.project_name SEPARATOR ', ') as projects
                    FROM users u
                    LEFT JOIN project_members pm ON pm.user_id = u.id
                    LEFT JOIN projects p ON p.id = pm.project_id AND p.is_active = 1
                    WHERE u.is_active = 1
                    GROUP BY u.id, u.username, u.role, u.email
                    ORDER BY u.role, u.username
                """))
            else:
                r = await db.execute(text("""
                    SELECT u.id, u.username, u.role, u.email,
                           GROUP_CONCAT(p.project_name SEPARATOR ', ') as projects
                    FROM users u
                    LEFT JOIN project_members pm ON pm.user_id = u.id
                    LEFT JOIN projects p ON p.id = pm.project_id AND p.is_active = 1
                    WHERE u.is_active = 1 AND u.role = :role
                    GROUP BY u.id, u.username, u.role, u.email
                    ORDER BY u.username
                """), {"role": role})
            members = r.mappings().all()

        if not members:
            return f"No members found with role='{role}'"

        lines = [f"## 团队成员列表 (role={role}, 共{len(members)}人)\n"]
        for m in members:
            proj_str = m["projects"] or "无项目"
            lines.append(f"- **{m['username']}** (id={m['id']}, {m['role']}) | 项目: {proj_str}")

        return "\n".join(lines)

    except Exception as e:
        return f"[ERROR] {e}"
