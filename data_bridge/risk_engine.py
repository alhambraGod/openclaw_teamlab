"""
OpenClaw TeamLab — Student Risk Score Engine
Computes multi-signal risk scores by mining CoEvo meeting report data.

Signals:
  1. Blocker Persistence (30%) — same blockers recurring across claw_meetings
  2. Goal Completion Gap (25%) — plans vs actual task completion
  3. Engagement Decline (20%) — report submission rate trend
  4. Sentiment Trajectory (15%) — emotion trend in reports
  5. Teacher Attention Signal (10%) — escalation language in teacher comments
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import openai
from sqlalchemy import text

from config.settings import settings
from config.database import get_db
from config.coevo_db import get_coevo_db

logger = logging.getLogger("teamlab.risk_engine")

# ── LLM Helper ───────────────────────────────────────────────────────

def _get_llm_client() -> openai.OpenAI:
    return openai.OpenAI(
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY or "unused",
    )


def _llm_json(prompt: str) -> dict:
    """Call LLM expecting JSON response. Returns parsed dict or empty."""
    try:
        client = _get_llm_client()
        resp = client.chat.completions.create(
            model=settings.LLM_FAST_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=2000,
        )
        content = resp.choices[0].message.content.strip()
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as e:
        logger.warning("[RiskEngine] LLM call failed: %s", e)
    return {}


# ── Signal Computation ────────────────────────────────────────────────

async def _get_student_reports(coevo_user_id: int, limit: int = 8) -> list[dict]:
    """Fetch recent pre-meeting reports for a student from CoEvo DB."""
    async with get_coevo_db() as session:
        result = await session.execute(
            text("""
                SELECT mr.id, mr.meeting_id, mr.phase,
                       mr.task_items, mr.key_blockers, mr.next_week_plan,
                       mr.remarks, mr.student_summary,
                       mr.teacher_suggestions, mr.teacher_comments,
                       m.meeting_name, m.meeting_time
                FROM meeting_reports mr
                JOIN meetings m ON mr.meeting_id = m.id
                WHERE mr.user_id = :uid
                ORDER BY m.meeting_time DESC
                LIMIT :lim
            """),
            {"uid": coevo_user_id, "lim": limit},
        )
        rows = result.mappings().all()
        return [dict(r) for r in rows]


async def _get_student_meeting_count(coevo_user_id: int) -> dict:
    """Count total claw_meetings and reports submitted."""
    async with get_coevo_db() as session:
        result = await session.execute(
            text("""
                SELECT
                    COUNT(DISTINCT ma.meeting_id) AS total_meetings,
                    COUNT(DISTINCT mr.meeting_id) AS reports_submitted
                FROM meeting_attendees ma
                LEFT JOIN meeting_reports mr
                    ON ma.meeting_id = mr.meeting_id AND ma.user_id = mr.user_id
                WHERE ma.user_id = :uid
            """),
            {"uid": coevo_user_id},
        )
        row = result.mappings().first()
        return dict(row) if row else {"total_meetings": 0, "reports_submitted": 0}


def _compute_blocker_persistence(reports: list[dict]) -> tuple[float, dict]:
    """Signal 1: Check if blockers recur across consecutive pre-reports."""
    pre_reports = [r for r in reports if r.get("phase") == "pre" and r.get("key_blockers")]
    if len(pre_reports) < 2:
        return 0.0, {"reason": "Insufficient pre-reports", "count": len(pre_reports)}

    # Use LLM to detect recurring blockers
    blockers_text = []
    for i, r in enumerate(pre_reports[:5]):
        blockers_text.append(f"Report {i+1} ({r.get('meeting_name', '?')}): {r['key_blockers']}")

    prompt = (
        "分析以下学生连续几次会前报告中提到的阻塞问题(blockers)。\n"
        "判断是否有重复出现的问题（语义相似即算重复，不需完全相同）。\n\n"
        + "\n".join(blockers_text) + "\n\n"
        "请用JSON回复: {\"recurring_count\": 重复问题数量, \"total_unique\": 去重后总问题数, "
        "\"recurring_issues\": [\"重复问题1\", ...], \"assessment\": \"简短评估\"}"
    )

    result = _llm_json(prompt)
    recurring = result.get("recurring_count", 0)
    total = result.get("total_unique", max(len(pre_reports), 1))

    score = min((recurring / max(total, 1)) * 100, 100)
    return score, {
        "recurring_count": recurring,
        "total_unique": total,
        "recurring_issues": result.get("recurring_issues", []),
        "assessment": result.get("assessment", ""),
    }


def _compute_goal_completion(reports: list[dict]) -> tuple[float, dict]:
    """Signal 2: Compare planned tasks vs actual completion."""
    pre_reports = [r for r in reports if r.get("phase") == "pre"]
    if len(pre_reports) < 2:
        return 0.0, {"reason": "Insufficient reports for comparison"}

    # Compare consecutive pairs: report[i].next_week_plan vs report[i-1].task_items
    pairs = []
    for i in range(len(pre_reports) - 1):
        plan = pre_reports[i + 1].get("next_week_plan", "")
        actual = pre_reports[i].get("task_items", "")
        if plan and actual:
            pairs.append({"planned": plan, "actual": actual})

    if not pairs:
        return 0.0, {"reason": "No plan-vs-actual pairs found"}

    # Use LLM to assess completion
    pair_text = "\n\n".join(
        f"计划({j+1}): {p['planned']}\n实际完成({j+1}): {p['actual']}"
        for j, p in enumerate(pairs[:3])
    )

    prompt = (
        "对比学生的计划和实际完成情况。\n\n" + pair_text + "\n\n"
        "请用JSON回复: {\"completion_rate\": 0到100的整数, \"assessment\": \"简短评估\"}"
    )

    result = _llm_json(prompt)
    completion_rate = result.get("completion_rate", 50)

    # Gap score: high gap = high risk
    gap_score = max(0, 100 - completion_rate)
    return gap_score, {
        "completion_rate": completion_rate,
        "pairs_analyzed": len(pairs),
        "assessment": result.get("assessment", ""),
    }


def _compute_engagement_decline(reports: list[dict], meeting_stats: dict) -> tuple[float, dict]:
    """Signal 3: Report submission rate trend."""
    total = meeting_stats.get("total_meetings", 0)
    submitted = meeting_stats.get("reports_submitted", 0)

    if total == 0:
        return 0.0, {"reason": "No claw_meetings attended"}

    rate = submitted / total
    # Low engagement = high risk
    score = max(0, (1 - rate) * 100)
    return score, {
        "total_meetings": total,
        "reports_submitted": submitted,
        "engagement_rate": round(rate, 2),
    }


def _compute_sentiment(reports: list[dict]) -> tuple[float, dict]:
    """Signal 4: Sentiment trajectory in recent reports."""
    texts = []
    for r in reports[:5]:
        parts = []
        if r.get("student_summary"):
            parts.append(r["student_summary"])
        if r.get("remarks"):
            parts.append(r["remarks"])
        if parts:
            texts.append(f"Report ({r.get('meeting_name', '?')}): {' '.join(parts)}")

    if not texts:
        return 0.0, {"reason": "No student text for sentiment analysis"}

    prompt = (
        "分析以下学生在连续几次会议中的文字表达，判断情绪趋势。\n\n"
        + "\n".join(texts) + "\n\n"
        "请用JSON回复: {\"sentiment_trend\": \"improving/stable/declining\", "
        "\"current_sentiment\": 0到100(100=非常积极), \"assessment\": \"简短评估\"}"
    )

    result = _llm_json(prompt)
    sentiment = result.get("current_sentiment", 50)
    trend = result.get("sentiment_trend", "stable")

    # Low sentiment + declining = high risk
    score = max(0, 100 - sentiment)
    if trend == "declining":
        score = min(score * 1.3, 100)
    elif trend == "improving":
        score = score * 0.7

    return round(score, 1), {
        "sentiment_trend": trend,
        "current_sentiment": sentiment,
        "assessment": result.get("assessment", ""),
    }


def _compute_teacher_signal(reports: list[dict]) -> tuple[float, dict]:
    """Signal 5: Escalation language in teacher comments."""
    teacher_texts = []
    for r in reports[:5]:
        if r.get("teacher_comments"):
            teacher_texts.append(r["teacher_comments"])
        if r.get("teacher_suggestions"):
            teacher_texts.append(r["teacher_suggestions"])

    if not teacher_texts:
        return 0.0, {"reason": "No teacher comments found"}

    # Keyword-based quick scan
    concern_keywords = [
        "需要改进", "落后", "关注", "担心", "不足", "加强", "加油",
        "需要提高", "进度慢", "需改善", "问题较多", "建议加快",
    ]
    combined = " ".join(teacher_texts)
    hits = sum(1 for kw in concern_keywords if kw in combined)
    score = min(hits * 25, 100)

    return score, {
        "concern_keywords_found": hits,
        "texts_analyzed": len(teacher_texts),
    }


# ── Main Risk Computation ─────────────────────────────────────────────

async def compute_student_risk(coevo_user_id: int, student_name: str = "") -> dict:
    """Compute the composite risk score for a single student.

    Returns:
        dict with overall_score, sub-signals, risk_level, explanation, signals_detail
    """
    reports = await _get_student_reports(coevo_user_id)
    meeting_stats = await _get_student_meeting_count(coevo_user_id)

    # Compute all 5 signals
    blocker_score, blocker_detail = _compute_blocker_persistence(reports)
    goal_score, goal_detail = _compute_goal_completion(reports)
    engagement_score, engagement_detail = _compute_engagement_decline(reports, meeting_stats)
    sentiment_score, sentiment_detail = _compute_sentiment(reports)
    teacher_score, teacher_detail = _compute_teacher_signal(reports)

    # Weighted composite
    overall = (
        blocker_score * 0.30
        + goal_score * 0.25
        + engagement_score * 0.20
        + sentiment_score * 0.15
        + teacher_score * 0.10
    )
    overall = round(min(overall, 100), 1)

    # Risk level
    if overall <= 30:
        risk_level = "green"
    elif overall <= 60:
        risk_level = "yellow"
    else:
        risk_level = "red"

    # Generate explanation
    top_signals = sorted([
        ("阻塞持续性", blocker_score),
        ("目标完成率差距", goal_score),
        ("参与度下降", engagement_score),
        ("情绪走势", sentiment_score),
        ("导师关注信号", teacher_score),
    ], key=lambda x: x[1], reverse=True)

    explanation_parts = []
    for name, score in top_signals[:3]:
        if score > 30:
            explanation_parts.append(f"{name}({score:.0f}分)")

    if explanation_parts:
        explanation = f"{student_name or '该学生'}当前风险等级为{risk_level}，主要风险信号：{'、'.join(explanation_parts)}。"
    else:
        explanation = f"{student_name or '该学生'}当前状态良好，无明显风险信号。"

    # Add detail from top signal
    if blocker_detail.get("recurring_issues"):
        explanation += f" 持续阻塞问题：{'、'.join(blocker_detail['recurring_issues'][:2])}。"
    if goal_detail.get("assessment"):
        explanation += f" {goal_detail['assessment']}"

    return {
        "coevo_user_id": coevo_user_id,
        "student_name": student_name,
        "overall_score": overall,
        "blocker_persistence": round(blocker_score, 1),
        "goal_completion_gap": round(goal_score, 1),
        "engagement_decline": round(engagement_score, 1),
        "sentiment_score": round(sentiment_score, 1),
        "teacher_signal": round(teacher_score, 1),
        "risk_level": risk_level,
        "explanation": explanation,
        "signals_detail": {
            "blocker": blocker_detail,
            "goal": goal_detail,
            "engagement": engagement_detail,
            "sentiment": sentiment_detail,
            "teacher": teacher_detail,
        },
    }


async def compute_all_risks() -> list[dict]:
    """Compute risk scores for all active claw_students and persist to DB."""
    # Get all claw_students from CoEvo
    async with get_coevo_db() as session:
        result = await session.execute(
            text("""
                SELECT DISTINCT u.id, u.username
                FROM users u
                JOIN project_members pm ON u.id = pm.user_id
                WHERE u.role = 'student' AND u.is_active = 1
            """)
        )
        claw_students = result.mappings().all()

    if not claw_students:
        logger.info("[RiskEngine] No active claw_students found")
        return []

    logger.info("[RiskEngine] Computing risk scores for %d claw_students", len(claw_students))
    all_risks = []

    for s in claw_students:
        try:
            risk = await compute_student_risk(s["id"], s["username"] or "")
            all_risks.append(risk)

            # Persist to DB
            async with get_db() as db_session:
                await db_session.execute(
                    text("""
                        INSERT INTO claw_student_risk_scores
                            (coevo_user_id, student_name, overall_score,
                             blocker_persistence, goal_completion_gap,
                             engagement_decline, sentiment_score, teacher_signal,
                             risk_level, explanation, signals_detail, computed_at)
                        VALUES
                            (:uid, :name, :overall, :blocker, :goal, :engagement,
                             :sentiment, :teacher, :level, :explanation,
                             :signals, NOW())
                    """),
                    {
                        "uid": risk["coevo_user_id"],
                        "name": risk["student_name"],
                        "overall": risk["overall_score"],
                        "blocker": risk["blocker_persistence"],
                        "goal": risk["goal_completion_gap"],
                        "engagement": risk["engagement_decline"],
                        "sentiment": risk["sentiment_score"],
                        "teacher": risk["teacher_signal"],
                        "level": risk["risk_level"],
                        "explanation": risk["explanation"],
                        "signals": json.dumps(risk["signals_detail"], ensure_ascii=False),
                    },
                )

            logger.debug("[RiskEngine] %s: score=%.1f level=%s",
                         risk["student_name"], risk["overall_score"], risk["risk_level"])

        except Exception as e:
            logger.error("[RiskEngine] Failed for user %s: %s", s["id"], e, exc_info=True)

    red_count = sum(1 for r in all_risks if r["risk_level"] == "red")
    yellow_count = sum(1 for r in all_risks if r["risk_level"] == "yellow")
    logger.info(
        "[RiskEngine] Done: %d claw_students, %d red, %d yellow, %d green",
        len(all_risks), red_count, yellow_count,
        len(all_risks) - red_count - yellow_count,
    )

    return all_risks
