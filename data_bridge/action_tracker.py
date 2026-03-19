"""
OpenClaw TeamLab — Cross-Meeting Action Item Tracker
Extracts action items from CoEvo meeting reports and tracks their lifecycle.
Detects stale items and reconciles completion status across claw_meetings.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

import openai
from sqlalchemy import text

from config.settings import settings
from config.database import get_db
from config.coevo_db import get_coevo_db

logger = logging.getLogger("teamlab.action_tracker")


def _get_llm_client() -> openai.OpenAI:
    return openai.OpenAI(
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY or "unused",
    )


async def extract_actions_from_meeting(meeting_id: int) -> list[dict]:
    """Extract structured action items from a meeting's pre-reports.

    Reads next_week_plan and task_items from all student pre-reports
    for the given meeting, uses LLM to extract structured actions.
    """
    async with get_coevo_db() as session:
        result = await session.execute(
            text("""
                SELECT mr.user_id, u.username,
                       mr.task_items, mr.next_week_plan
                FROM meeting_reports mr
                JOIN users u ON mr.user_id = u.id
                WHERE mr.meeting_id = :mid AND mr.phase = 'pre'
            """),
            {"mid": meeting_id},
        )
        reports = result.mappings().all()

    if not reports:
        return []

    all_actions = []
    for r in reports:
        plans = r.get("next_week_plan", "") or ""
        tasks = r.get("task_items", "") or ""
        combined = f"计划: {plans}\n已完成任务: {tasks}"

        if not combined.strip():
            continue

        # Use LLM to extract structured actions
        prompt = (
            f"从以下学生报告中提取具体的待办事项（action items）。\n"
            f"学生: {r.get('username', '?')}\n"
            f"内容: {combined[:500]}\n\n"
            f"请用JSON数组格式回复: "
            f'[{{"action": "具体待办", "priority": "high/medium/low", "deadline": "截止时间或空字符串"}}]'
        )

        try:
            client = _get_llm_client()
            resp = client.chat.completions.create(
                model=settings.LLM_FAST_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=1000,
            )
            content = resp.choices[0].message.content.strip()
            match = re.search(r"\[.*\]", content, re.DOTALL)
            if match:
                items = json.loads(match.group())
                for item in items:
                    all_actions.append({
                        "coevo_user_id": r["user_id"],
                        "assignee_name": r.get("username", ""),
                        "source_meeting_id": meeting_id,
                        "action_text": item.get("action", ""),
                        "priority": item.get("priority", "medium"),
                        "deadline": item.get("deadline", ""),
                    })
        except Exception as e:
            logger.warning("[ActionTracker] LLM extraction failed for user %s: %s", r["user_id"], e)

    # Save to DB
    saved = 0
    for action in all_actions:
        if not action["action_text"]:
            continue
        try:
            async with get_db() as session:
                await session.execute(
                    text("""
                        INSERT INTO claw_action_item_tracker
                            (coevo_user_id, source_meeting_id, action_text,
                             assignee_name, deadline, priority, status, created_at)
                        VALUES
                            (:uid, :mid, :action, :name, :deadline, :priority, 'open', NOW())
                    """),
                    {
                        "uid": action["coevo_user_id"],
                        "mid": action["source_meeting_id"],
                        "action": action["action_text"],
                        "name": action["assignee_name"],
                        "deadline": action["deadline"],
                        "priority": action["priority"],
                    },
                )
                saved += 1
        except Exception as e:
            logger.warning("[ActionTracker] Failed to save action: %s", e)

    logger.info("[ActionTracker] Extracted %d actions from meeting %d, saved %d",
                len(all_actions), meeting_id, saved)
    return all_actions


async def reconcile_all_actions() -> dict:
    """Check open action items against newer reports to detect completion.

    Also marks items older than 21 days as stale.
    """
    stale_threshold = datetime.utcnow() - timedelta(days=21)
    resolved = 0
    staled = 0

    try:
        # 1. Mark old open items as stale
        async with get_db() as session:
            result = await session.execute(
                text("""
                    UPDATE claw_action_item_tracker
                    SET status = 'stale', stale_since = NOW()
                    WHERE status = 'open' AND created_at < :threshold
                """),
                {"threshold": stale_threshold},
            )
            staled = result.rowcount or 0

        # 2. For remaining open items, check if student's newer reports mention completion
        async with get_db() as session:
            result = await session.execute(
                text("""
                    SELECT id, coevo_user_id, action_text, source_meeting_id
                    FROM claw_action_item_tracker
                    WHERE status = 'open'
                    ORDER BY created_at ASC
                    LIMIT 50
                """)
            )
            open_items = result.mappings().all()

        for item in open_items:
            uid = item["coevo_user_id"]
            action = item["action_text"]

            # Get newer reports for this student
            try:
                async with get_coevo_db() as session:
                    result = await session.execute(
                        text("""
                            SELECT mr.task_items, mr.meeting_id, m.meeting_name
                            FROM meeting_reports mr
                            JOIN meetings m ON mr.meeting_id = m.id
                            WHERE mr.user_id = :uid AND mr.phase = 'pre'
                              AND mr.meeting_id > :src_mid
                            ORDER BY m.meeting_time ASC
                            LIMIT 3
                        """),
                        {"uid": uid, "src_mid": item["source_meeting_id"]},
                    )
                    newer_reports = result.mappings().all()
            except Exception:
                continue

            if not newer_reports:
                continue

            # Simple semantic check: does any newer task_items mention this action?
            combined_tasks = " ".join(
                r.get("task_items", "") or "" for r in newer_reports
            )
            if not combined_tasks:
                continue

            # Quick keyword overlap check
            action_words = set(action.replace("，", " ").replace("、", " ").split())
            task_words = set(combined_tasks.replace("，", " ").replace("、", " ").split())
            overlap = len(action_words & task_words) / max(len(action_words), 1)

            if overlap > 0.3:
                # Mark as done
                async with get_db() as session:
                    await session.execute(
                        text("""
                            UPDATE claw_action_item_tracker
                            SET status = 'done',
                                resolved_meeting_id = :rmid,
                                resolution_evidence = :evidence,
                                updated_at = NOW()
                            WHERE id = :aid
                        """),
                        {
                            "aid": item["id"],
                            "rmid": newer_reports[0]["meeting_id"],
                            "evidence": f"Matched in task_items (overlap={overlap:.2f})",
                        },
                    )
                    resolved += 1

    except Exception as e:
        logger.error("[ActionTracker] Reconciliation failed: %s", e, exc_info=True)

    stats = {"resolved": resolved, "staled": staled, "checked": len(open_items) if 'open_items' in dir() else 0}
    logger.info("[ActionTracker] Reconciliation: %s", stats)
    return stats


async def extract_actions_from_recent_meetings(limit: int = 5) -> int:
    """Extract actions from the N most recent completed claw_meetings that haven't been processed yet."""
    async with get_coevo_db() as session:
        result = await session.execute(
            text("""
                SELECT id FROM meetings
                WHERE status = 'completed' AND is_active = 1
                ORDER BY meeting_time DESC
                LIMIT :lim
            """),
            {"lim": limit},
        )
        meetings_list = result.mappings().all()

    total = 0
    for m in meetings_list:
        # Check if already extracted
        async with get_db() as session:
            result = await session.execute(
                text("SELECT COUNT(*) AS cnt FROM claw_action_item_tracker WHERE source_meeting_id = :mid"),
                {"mid": m["id"]},
            )
            row = result.mappings().first()
            if row and row["cnt"] > 0:
                continue

        actions = await extract_actions_from_meeting(m["id"])
        total += len(actions)

    return total
