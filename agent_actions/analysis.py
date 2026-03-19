"""
Agent Actions — 分析类（合作推荐、风险评估、成长叙事等）。
"""
import asyncio
import json
import logging
import re
from typing import Optional

from agent_actions._helpers import resolve_user

logger = logging.getLogger("agent_actions.analysis")


async def compute_student_risk(student_name: Optional[str] = None) -> str:
    try:
        from sqlalchemy import text
        from config.coevo_db import get_coevo_db
        from data_bridge.risk_engine import compute_student_risk as _compute_one, compute_all_risks

        if student_name:
            async with get_coevo_db() as db:
                user, note = await resolve_user(student_name, db)

            if not user:
                return note  # 含 NOT FOUND 说明
            prefix = f"{note}\n" if note else ""

            risk = await _compute_one(user["id"], user["username"])
            lines = [
                prefix + f"## 风险评估: {risk['student_name']}",
                f"- **综合风险分**: {risk['overall_score']} | **级别**: {risk['risk_level'].upper()}",
                f"- 阻塞持续性: {risk['blocker_persistence']} (权重30%)",
                f"- 目标完成差距: {risk['goal_completion_gap']} (权重25%)",
                f"- 参与度下降: {risk['engagement_decline']} (权重20%)",
                f"- 情绪走势: {risk['sentiment_score']} (权重15%)",
                f"- 导师关注信号: {risk['teacher_signal']} (权重10%)",
                f"\n**分析说明**: {risk['explanation']}",
            ]
            detail = risk.get("signals_detail", {})
            if detail.get("blocker", {}).get("recurring_issues"):
                issues = "、".join(detail["blocker"]["recurring_issues"][:3])
                lines.append(f"\n**持续阻塞问题**: {issues}")
            if detail.get("goal", {}).get("assessment"):
                lines.append(f"**目标完成评估**: {detail['goal']['assessment']}")
            if detail.get("sentiment", {}).get("sentiment_trend"):
                lines.append(f"**情绪趋势**: {detail['sentiment']['sentiment_trend']}")

            return "\n".join(lines)

        else:
            all_risks = await compute_all_risks()
            if not all_risks:
                return "(No active claw_students found)"

            all_risks.sort(key=lambda x: x["overall_score"], reverse=True)
            red = [r for r in all_risks if r["risk_level"] == "red"]
            yellow = [r for r in all_risks if r["risk_level"] == "yellow"]
            green = [r for r in all_risks if r["risk_level"] == "green"]

            lines = [
                f"## 全团队风险评估 ({len(all_risks)} 名学生)",
                f"红色 {len(red)} | 黄色 {len(yellow)} | 绿色 {len(green)}\n",
            ]
            if red:
                lines.append("### 🔴 需要立即关注")
                for r in red:
                    lines.append(f"- **{r['student_name']}**: {r['overall_score']}分 — {r['explanation'][:150]}")
            if yellow:
                lines.append("\n### 🟡 需要持续观察")
                for r in yellow:
                    lines.append(f"- **{r['student_name']}**: {r['overall_score']}分 — {r['explanation'][:100]}")
            if green:
                lines.append("\n### 🟢 状态良好")
                for r in green:
                    lines.append(f"- {r['student_name']}: {r['overall_score']}分")

            return "\n".join(lines)

    except Exception as e:
        logger.error("compute_student_risk failed: %s", e, exc_info=True)
        return f"[ERROR] {e}"


async def generate_growth_narrative(student_name: str, months: int = 3) -> str:
    try:
        from sqlalchemy import text
        from config.coevo_db import get_coevo_db
        from data_bridge.narrative import generate_student_narrative

        async with get_coevo_db() as db:
            user, note = await resolve_user(student_name, db)

        if not user:
            return note
        prefix = f"{note}\n" if note else ""

        months = min(max(months, 1), 12)
        result = await generate_student_narrative(user["id"], months)

        lines = [
            prefix + f"## {result['student_name']} — 成长叙事 (近{months}个月)\n",
            result["narrative"],
        ]
        if result.get("key_milestones"):
            lines.append("\n### 关键里程碑")
            for m in result["key_milestones"]:
                lines.append(f"- {m}")
        if result.get("current_assessment"):
            lines.append(f"\n### 当前综合评估\n{result['current_assessment']}")
        if result.get("recommendations"):
            lines.append("\n### 发展建议")
            for rec in result["recommendations"]:
                lines.append(f"- {rec}")

        return "\n".join(lines)

    except Exception as e:
        logger.error("generate_growth_narrative failed: %s", e, exc_info=True)
        return f"[ERROR] {e}"


async def compute_collaboration_score(person_a: str, person_b: str) -> str:
    try:
        from sqlalchemy import text
        from config.coevo_db import get_coevo_db
        from config.database import get_db
        from data_bridge.risk_engine import _get_student_reports
        from config.settings import settings

        async with get_coevo_db() as db:
            user_a, note_a = await resolve_user(person_a, db)
            user_b, note_b = await resolve_user(person_b, db)

        if not user_a:
            return note_a
        if not user_b:
            return note_b
        prefix = ""
        if note_a:
            prefix += f"{note_a}\n"
        if note_b:
            prefix += f"{note_b}\n"

        name_a, name_b = user_a["username"], user_b["username"]
        reports_a = await _get_student_reports(user_a["id"])
        reports_b = await _get_student_reports(user_b["id"])

        async with get_coevo_db() as db:
            r = await db.execute(text("""
                SELECT pm.quarterly_goal, pm.short_term_goal, p.project_name
                FROM project_members pm JOIN projects p ON p.id=pm.project_id AND p.is_active=1
                WHERE pm.user_id=:uid
            """), {"uid": user_a["id"]})
            goals_a = r.mappings().all()
            r = await db.execute(text("""
                SELECT pm.quarterly_goal, pm.short_term_goal, p.project_name
                FROM project_members pm JOIN projects p ON p.id=pm.project_id AND p.is_active=1
                WHERE pm.user_id=:uid
            """), {"uid": user_b["id"]})
            goals_b = r.mappings().all()

        async with get_db() as tdb:
            # Resolve coevo_user_id -> openclaw student_id via claw_coevo_student_links
            r = await tdb.execute(text("""
                SELECT openclaw_student_id FROM claw_coevo_student_links WHERE coevo_user_id = :uid LIMIT 1
            """), {"uid": user_a["id"]})
            link_a = r.mappings().first()
            r = await tdb.execute(text("""
                SELECT openclaw_student_id FROM claw_coevo_student_links WHERE coevo_user_id = :uid LIMIT 1
            """), {"uid": user_b["id"]})
            link_b = r.mappings().first()

            caps_a, caps_b = {}, {}
            if link_a:
                r = await tdb.execute(text("""
                    SELECT cd.name, cs.score FROM claw_capability_scores cs
                    JOIN claw_capability_dimensions cd ON cs.dimension_id = cd.id
                    WHERE cs.student_id = :sid ORDER BY cs.assessed_at DESC
                """), {"sid": link_a["openclaw_student_id"]})
                seen = set()
                for row in r.mappings().all():
                    if row["name"] not in seen:
                        caps_a[row["name"]] = float(row["score"])
                        seen.add(row["name"])
            if link_b:
                r = await tdb.execute(text("""
                    SELECT cd.name, cs.score FROM claw_capability_scores cs
                    JOIN claw_capability_dimensions cd ON cs.dimension_id = cd.id
                    WHERE cs.student_id = :sid ORDER BY cs.assessed_at DESC
                """), {"sid": link_b["openclaw_student_id"]})
                seen = set()
                for row in r.mappings().all():
                    if row["name"] not in seen:
                        caps_b[row["name"]] = float(row["score"])
                        seen.add(row["name"])

        def summarize_reports(reports, max_n=4):
            lines = []
            for rpt in reports[:max_n]:
                parts = []
                if rpt.get("key_blockers"):
                    parts.append(f"阻塞: {rpt['key_blockers'][:150]}")
                if rpt.get("task_items"):
                    parts.append(f"完成: {rpt['task_items'][:100]}")
                if parts:
                    lines.append(" | ".join(parts))
            return "\n".join(lines) if lines else "暂无数据"

        goals_str_a = "; ".join(
            f"{g['project_name']}: {g['quarterly_goal'] or g['short_term_goal'] or '?'}"
            for g in goals_a
        ) or "暂无"
        goals_str_b = "; ".join(
            f"{g['project_name']}: {g['quarterly_goal'] or g['short_term_goal'] or '?'}"
            for g in goals_b
        ) or "暂无"
        caps_str_a = ", ".join(f"{k}:{v:.0f}" for k, v in caps_a.items()) or "暂无能力数据"
        caps_str_b = ", ".join(f"{k}:{v:.0f}" for k, v in caps_b.items()) or "暂无能力数据"

        prompt = f"""你是一位资深科研导师，请基于以下真实数据，深度分析 {name_a} 和 {name_b} 的合作价值。

## {name_a}
- 简介: {user_a.get('bio', '无')}
- 项目目标: {goals_str_a}
- 能力评分: {caps_str_a}
- 近期会议报告:
{summarize_reports(reports_a)}

## {name_b}
- 简介: {user_b.get('bio', '无')}
- 项目目标: {goals_str_b}
- 能力评分: {caps_str_b}
- 近期会议报告:
{summarize_reports(reports_b)}

请从以下维度分析合作价值并给出0-100的综合得分：
1. 能力互补性
2. 目标协同性
3. 障碍解锁潜力
4. 综合合作价值评估

请用以下JSON格式回复：
{{
  "score": 0-100,
  "capability_complementarity": "分析...",
  "goal_synergy": "分析...",
  "blocker_unlock": "分析...",
  "overall_recommendation": "综合推荐意见...",
  "collaboration_ideas": ["具体合作方向1", "具体合作方向2"]
}}"""

        import openai
        client = openai.AsyncOpenAI(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY or "unused",
        )
        resp = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=2000,
        )
        content = resp.choices[0].message.content.strip()
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            raw = match.group()
            raw = re.sub(r",\s*}", "}", raw)
            raw = re.sub(r",\s*]", "]", raw)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {}
        else:
            return f"[LLM] {content}"

        lines = [
            prefix + f"## 合作价值分析: {name_a} × {name_b}",
            f"**综合合作价值分: {data.get('score', '?')}/100**\n",
            f"### 能力互补性\n{data.get('capability_complementarity', '')}",
            f"\n### 目标协同性\n{data.get('goal_synergy', '')}",
            f"\n### 障碍解锁潜力\n{data.get('blocker_unlock', '')}",
            f"\n### 综合推荐\n{data.get('overall_recommendation', '')}",
        ]
        ideas = data.get("collaboration_ideas", [])
        if ideas:
            lines.append("\n### 具体合作方向建议")
            for idea in ideas:
                lines.append(f"- {idea}")

        return "\n".join(lines)

    except Exception as e:
        logger.error("compute_collaboration_score failed: %s", e, exc_info=True)
        return f"[ERROR] {e}"


async def find_best_collaborators(person_name: str, top_k: int = 5) -> str:
    """
    为某人推荐最佳合作者。
    策略：优先读取已有协作推荐记录（数据驱动，毫秒级）；无记录时再触发 LLM 批量评估。
    模糊匹配（非精确命中）时，返回 [NOT_FOUND] + 候选名单让用户确认，不直接做 LLM 分析。
    """
    try:
        from sqlalchemy import text
        from config.coevo_db import get_coevo_db
        from config.settings import settings
        import openai

        # ── Step 1: 查找目标用户 ──
        async with get_coevo_db() as db:
            target, note = await resolve_user(person_name, db)

        if not target:
            # resolve_user 已附带候选名单，直接返回（上层转 HTTP 404）
            return note

        uid = target["id"]
        target_name = target["username"]

        # 若为模糊匹配结果（名字与查询不同），先让用户确认，避免跑错人的 LLM 分析
        if target_name != person_name:
            from agent_actions._helpers import suggest_users
            async with get_coevo_db() as db:
                candidates = await suggest_users(person_name, db, limit=5)
            # 将实际匹配到的人名也加入候选列表首位
            if target_name not in candidates:
                candidates.insert(0, target_name)
            candidate_str = "、".join(f"'{c}'" for c in candidates[:5])
            return (
                f"[NOT_FOUND] 系统中未找到「{person_name}」，"
                f"最相似的成员是：{candidate_str}。\n"
                "请让用户确认正确的名字后重试。"
            )

        prefix = ""

        # ── Step 2: 优先从 coevo_prod 已有推荐记录读取（数据驱动，无需 LLM）──
        # 注意：coevo_prod 中表名为 collaboration_recommendations（无 claw_ 前缀）
        async with get_coevo_db() as db:
            r = await db.execute(text("""
                SELECT cr.target_user_ids, cr.best_partner_analysis,
                       cr.collaboration_direction, cr.collaboration_suggestion,
                       cr.project_id
                FROM collaboration_recommendations cr
                WHERE cr.requester_user_id = :uid
                  AND cr.status = 'completed'
                  AND cr.best_partner_analysis IS NOT NULL
                ORDER BY cr.created_at DESC
                LIMIT 20
            """), {"uid": uid})
            existing = r.mappings().all()

        # 补充项目名
        project_names: dict[int, str] = {}
        if existing:
            pids = {row["project_id"] for row in existing if row.get("project_id")}
            if pids:
                ph2 = ",".join(f":pid{i}" for i, _ in enumerate(pids))
                async with get_coevo_db() as db:
                    r = await db.execute(
                        text(f"SELECT id, project_name FROM projects WHERE id IN ({ph2})"),
                        {f"pid{i}": pid for i, pid in enumerate(pids)},
                    )
                    project_names = {row["id"]: row["project_name"] for row in r.mappings().all()}

        if existing:
            # aiomysql JSON 列返回字符串，需要解析
            def _parse_tids(raw):
                if isinstance(raw, list):
                    return raw
                if isinstance(raw, str):
                    try:
                        v = json.loads(raw)
                        return v if isinstance(v, list) else []
                    except (json.JSONDecodeError, ValueError):
                        return []
                return []

            # 收集推荐的合作者 ID
            partner_ids = set()
            for row in existing:
                for tid in _parse_tids(row.get("target_user_ids")):
                    try:
                        partner_ids.add(int(tid))
                    except (ValueError, TypeError):
                        pass

            id_to_name = {}
            if partner_ids:
                ids_list = list(partner_ids)
                ph = ",".join(f":pid{i}" for i in range(len(ids_list)))
                async with get_coevo_db() as db:
                    r = await db.execute(
                        text(f"SELECT id, username FROM users WHERE id IN ({ph})"),
                        {f"pid{i}": x for i, x in enumerate(ids_list)},
                    )
                    id_to_name = {row["id"]: row["username"] for row in r.mappings().all()}

            def _why(row) -> str:
                raw = row.get("best_partner_analysis")
                analysis = raw
                if isinstance(raw, str):
                    try:
                        analysis = json.loads(raw)
                    except (json.JSONDecodeError, ValueError):
                        return raw[:250] if raw else ""
                if isinstance(analysis, dict):
                    return (analysis.get("why_best") or analysis.get("overall_recommendation") or "")[:250]
                return ""

            # 按项目分组、展示推荐
            lines = [prefix + f"## {target_name} — 最佳合作者推荐\n（基于已有 {len(existing)} 条协作推荐记录，数据驱动）\n"]
            seen_partners: set = set()
            count = 0
            for row in existing:
                tids = _parse_tids(row.get("target_user_ids"))
                partners = []
                for x in tids:
                    try:
                        partners.append(id_to_name.get(int(x), str(x)))
                    except (ValueError, TypeError):
                        pass
                if not partners:
                    continue
                pname = project_names.get(row.get("project_id")) or "未知项目"
                why = _why(row)
                direction = (row.get("collaboration_direction") or "")[:150]
                key = tuple(sorted(partners))
                if key in seen_partners:
                    continue
                seen_partners.add(key)
                count += 1
                lines.append(f"**#{count} {', '.join(partners)}**（{pname}）")
                if why:
                    lines.append(f"  {why}")
                elif direction:
                    lines.append(f"  合作方向: {direction}")
                lines.append("")
                if count >= top_k:
                    break

            lines.append("\n> 如需深入分析特定组合，可调用 compute_collaboration_score()")
            return "\n".join(lines)

        # ── Step 3: 无现成记录时，LLM 批量评估（异步，不阻塞事件循环）──
        logger.info("find_best_collaborators: no existing records for %s, using LLM", target_name)

        # 限制候选人数，避免 prompt 过长导致超时
        MAX_CANDIDATES_FOR_LLM = 20
        async with get_coevo_db() as db:
            r = await db.execute(text("""
                SELECT id, username, role FROM users
                WHERE is_active=1 AND id != :tid
                ORDER BY username
                LIMIT :lim
            """), {"tid": uid, "lim": MAX_CANDIDATES_FOR_LLM})
            candidates = r.mappings().all()

        if not candidates:
            return "(No other team members found)"

        async with get_coevo_db() as db:
            r = await db.execute(text("""
                SELECT pm.quarterly_goal, pm.short_term_goal, p.project_name
                FROM project_members pm JOIN projects p ON p.id=pm.project_id AND p.is_active=1
                WHERE pm.user_id=:uid
            """), {"uid": uid})
            target_goals = r.mappings().all()

        async with get_coevo_db() as db:
            r = await db.execute(text("""
                SELECT mr.key_blockers FROM meeting_reports mr
                JOIN meetings m ON mr.meeting_id = m.id
                WHERE mr.user_id = :uid AND mr.key_blockers IS NOT NULL
                ORDER BY m.meeting_time DESC LIMIT 5
            """), {"uid": uid})
            target_reports = r.mappings().all()

        target_blockers = [row["key_blockers"][:150] for row in target_reports if row.get("key_blockers")]

        # 批量取候选者数据（一次查询替代 N 次）
        cand_ids = [c["id"] for c in candidates]
        ph = ",".join(f":id{i}" for i in range(len(cand_ids)))
        params = {f"id{i}": v for i, v in enumerate(cand_ids)}

        async with get_coevo_db() as db:
            r = await db.execute(text(f"""
                SELECT pm.user_id, pm.quarterly_goal, pm.short_term_goal, p.project_name
                FROM project_members pm JOIN projects p ON p.id=pm.project_id AND p.is_active=1
                WHERE pm.user_id IN ({ph})
            """), params)
            all_goals = r.mappings().all()

        async with get_coevo_db() as db:
            r = await db.execute(text(f"""
                SELECT mr.user_id, mr.key_blockers
                FROM meeting_reports mr
                JOIN meetings m ON mr.meeting_id = m.id
                WHERE mr.user_id IN ({ph}) AND mr.key_blockers IS NOT NULL
                ORDER BY m.meeting_time DESC
            """), params)
            all_reports = r.mappings().all()

        goals_by_uid: dict[int, list] = {}
        for g in all_goals:
            goals_by_uid.setdefault(g["user_id"], []).append(g)

        blockers_by_uid: dict[int, list] = {}
        for rep in all_reports:
            xid = rep["user_id"]
            if len(blockers_by_uid.get(xid, [])) < 2:
                blockers_by_uid.setdefault(xid, []).append(rep["key_blockers"][:100])

        candidate_summaries = []
        for cand in candidates:
            cid = cand["id"]
            goals = goals_by_uid.get(cid, [])
            goal_str = "; ".join(
                f"{g['project_name']}: {g['quarterly_goal'] or g['short_term_goal'] or ''}"
                for g in goals[:2]
            )[:200]
            candidate_summaries.append({
                "id": cid,
                "name": cand["username"],
                "role": cand["role"],
                "goals": goal_str or "无",
                "blockers": "; ".join(blockers_by_uid.get(cid, [])) or "无",
            })

        target_goals_str = "; ".join(
            f"{g['project_name']}: {g['quarterly_goal'] or g['short_term_goal'] or ''}"
            for g in target_goals
        )[:300]

        candidates_text = "\n".join(
            f"{i+1}. {c['name']}({c['role']}): 目标={c['goals'][:150]}; 阻塞={c['blockers'][:100]}"
            for i, c in enumerate(candidate_summaries)
        )

        prompt = f"""你是科研合作专家。请评估以下{len(candidate_summaries)}位候选人与{target_name}的合作价值。

## {target_name}的情况
- 目标: {target_goals_str or '暂无'}
- 近期阻塞问题: {'; '.join(target_blockers[:3]) or '暂无'}

## 候选合作者列表
{candidates_text}

请对每位候选人打分（0-100），考虑能力互补、障碍解锁、研究协同。
严格用JSON回复：
{{
  "rankings": [
    {{"name": "姓名", "score": 85, "reason": "一句话核心理由"}},
    ...
  ]
}}"""

        client = openai.AsyncOpenAI(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY or "unused",
        )
        LLM_CALL_TIMEOUT = 45  # 单次 LLM 调用上限 45s，给外层留余量
        try:
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=settings.LLM_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=2000,
                ),
                timeout=LLM_CALL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("find_best_collaborators: LLM call timed out after %ss", LLM_CALL_TIMEOUT)
            return (
                f"[TIMEOUT] 合作者分析需要评估 {len(candidate_summaries)} 位成员，耗时超过预期。\n"
                "建议通过 POST /api/chat 提交为异步任务，完成后将自动通知您。"
            )
        content = resp.choices[0].message.content.strip()
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            return f"[LLM parsing error] {content[:500]}"

        raw = match.group()
        raw = re.sub(r",\s*}", "}", raw)
        raw = re.sub(r",\s*]", "]", raw)
        rankings = []
        try:
            data = json.loads(raw)
            rankings = data.get("rankings", [])
        except json.JSONDecodeError:
            items = re.findall(
                r'\{"name"\s*:\s*"([^"]+)"\s*,\s*"score"\s*:\s*(\d+)\s*,\s*"reason"\s*:\s*"([^"]*)"\}',
                raw,
            )
            rankings = [{"name": m[0], "score": int(m[1]), "reason": m[2]} for m in items]
        if not rankings:
            return f"[LLM] 未能解析出有效排名，请重试。原始片段: {content[:300]}"
        rankings = sorted(rankings, key=lambda x: x.get("score", 0) if isinstance(x.get("score"), (int, float)) else 0, reverse=True)

        lines = [
            prefix + f"## {target_name} — 最佳合作者推荐 (Top {top_k})\n",
            f"（LLM 综合评估 {len(candidate_summaries)} 位候选人）\n",
        ]
        for i, rank in enumerate(rankings[:top_k], 1):
            lines.append(f"**#{i} {rank.get('name', '?')}** — {rank.get('score', '?')}/100")
            if rank.get("reason"):
                lines.append(f"  {rank['reason']}\n")

        lines.append("\n> 如需深入分析特定组合，可调用 compute_collaboration_score()")
        return "\n".join(lines)

    except Exception as e:
        logger.error("find_best_collaborators failed: %s", e, exc_info=True)
        return f"[ERROR] {e}"


async def get_action_items(status_filter: str = "open,stale") -> str:
    try:
        from config.database import get_db
        from sqlalchemy import text

        statuses = [s.strip() for s in status_filter.split(",")]
        placeholders = ",".join(f":s{i}" for i in range(len(statuses)))
        params = {f"s{i}": s for i, s in enumerate(statuses)}

        async with get_db() as db:
            r = await db.execute(text(f"""
                SELECT assignee_name, action_text, status, due_date,
                       source_meeting_id, created_at
                FROM claw_action_item_tracker
                WHERE status IN ({placeholders})
                ORDER BY
                    CASE status WHEN 'stale' THEN 0 WHEN 'open' THEN 1 ELSE 2 END,
                    due_date ASC
                LIMIT 50
            """), params)
            items = r.mappings().all()

        if not items:
            return f"(No action items with status: {status_filter})"

        stale = [i for i in items if i["status"] == "stale"]
        open_items = [i for i in items if i["status"] == "open"]
        others = [i for i in items if i["status"] not in ("stale", "open")]

        lines = [f"## 待办事项 (共{len(items)}条)\n"]

        if stale:
            lines.append(f"### ⚠️ 已超期未完成 ({len(stale)}条)")
            for item in stale:
                due = str(item["due_date"] or "")[:10] or "无截止日"
                lines.append(f"- **{item['assignee_name']}**: {item['action_text'][:100]} (截止: {due})")

        if open_items:
            lines.append(f"\n### 📌 进行中 ({len(open_items)}条)")
            for item in open_items:
                due = str(item["due_date"] or "")[:10] or "无截止日"
                lines.append(f"- **{item['assignee_name']}**: {item['action_text'][:100]} (截止: {due})")

        if others:
            lines.append(f"\n### 其他 ({len(others)}条)")
            for item in others:
                lines.append(f"- [{item['status']}] {item['assignee_name']}: {item['action_text'][:80]}")

        return "\n".join(lines)

    except Exception as e:
        return f"[ERROR] {e}"
