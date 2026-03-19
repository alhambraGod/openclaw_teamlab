"""
Agent Actions — 写入类（洞见记录、协作推荐落库等）。
仅写入 openclaw_teamlab DB，严禁写入 cognalign_coevo_prod。
"""
import json
import logging
from typing import Optional

logger = logging.getLogger("agent_actions.write")


async def log_insight(
    insight_type: str,
    content: str,
    subject: Optional[str] = None,
    title: Optional[str] = None,
    related_persons: Optional[list] = None,
    importance: str = "medium",
) -> str:
    """持久化洞见到 TeamLab DB。"""
    try:
        from sqlalchemy import text
        from config.database import get_db

        t = title or subject or content[:80]
        async with get_db() as db:
            await db.execute(text("""
                INSERT INTO claw_pi_agent_insights
                    (insight_type, title, content, related_persons,
                     importance, generated_by, created_at)
                VALUES
                    (:itype, :title, :content, :persons, :importance,
                     'csi-openclaw', NOW())
            """), {
                "itype": insight_type,
                "title": t,
                "content": content,
                "persons": json.dumps(related_persons or [], ensure_ascii=False),
                "importance": importance,
            })

        return f"Insight logged [{importance}]: {t}"

    except Exception as e:
        try:
            from sqlalchemy import text
            from config.database import get_db
            t = title or subject or content[:80]
            async with get_db() as db:
                await db.execute(text("""
                    INSERT INTO pi_agent_analysis_log
                        (analysis_type, subject_a, subject_b, score, content, created_at)
                    VALUES (:itype, :title, :persons, NULL, :content, NOW())
                """), {
                    "itype": insight_type,
                    "title": t,
                    "persons": json.dumps(related_persons or []),
                    "content": content,
                })
            return f"Insight logged (fallback): {t}"
        except Exception:
            pass
        logger.warning("log_insight DB write failed: %s", e)
        return f"[WARN] Could not persist insight to DB ({e})"


async def save_collaboration_recommendation(
    person_a_name: str,
    person_b_name: str,
    score: float,
    reasoning: str,
    collaboration_ideas: list,
) -> str:
    """保存协作推荐结果到 TeamLab DB。"""
    try:
        from sqlalchemy import text
        from config.coevo_db import get_coevo_db
        from config.database import get_db

        async with get_coevo_db() as db:
            r_a = await db.execute(
                text("SELECT id, username FROM users WHERE username LIKE :n AND is_active=1 LIMIT 1"),
                {"n": f"%{person_a_name}%"},
            )
            r_b = await db.execute(
                text("SELECT id, username FROM users WHERE username LIKE :n AND is_active=1 LIMIT 1"),
                {"n": f"%{person_b_name}%"},
            )
            user_a = r_a.mappings().first()
            user_b = r_b.mappings().first()

        if not user_a or not user_b:
            return f"[ERROR] Could not resolve users: '{person_a_name}', '{person_b_name}'"

        async with get_db() as db:
            await db.execute(text("""
                INSERT INTO pi_collaboration_insights
                    (person_a_id, person_a_name, person_b_id, person_b_name,
                     collaboration_score, reasoning, collaboration_ideas,
                     generated_by, created_at)
                VALUES
                    (:aid, :aname, :bid, :bname, :score, :reasoning, :ideas,
                     'csi-openclaw', NOW())
                ON DUPLICATE KEY UPDATE
                    collaboration_score = :score,
                    reasoning = :reasoning,
                    collaboration_ideas = :ideas,
                    created_at = NOW()
            """), {
                "aid": user_a["id"],
                "aname": user_a["username"],
                "bid": user_b["id"],
                "bname": user_b["username"],
                "score": score,
                "reasoning": reasoning,
                "ideas": json.dumps(collaboration_ideas, ensure_ascii=False),
            })

        return f"Saved collaboration insight: {user_a['username']} × {user_b['username']} (score={score})"

    except Exception as e:
        try:
            from sqlalchemy import text
            from config.coevo_db import get_coevo_db
            from config.database import get_db

            async with get_coevo_db() as db:
                r = await db.execute(
                    text("SELECT id, username FROM users WHERE username LIKE :n AND is_active=1 LIMIT 1"),
                    {"n": f"%{person_a_name}%"},
                )
                user_a = r.mappings().first()
                r = await db.execute(
                    text("SELECT id, username FROM users WHERE username LIKE :n AND is_active=1 LIMIT 1"),
                    {"n": f"%{person_b_name}%"},
                )
                user_b = r.mappings().first()

            if user_a and user_b:
                async with get_db() as db:
                    await db.execute(text("""
                        INSERT INTO pi_agent_analysis_log
                            (analysis_type, subject_a, subject_b, score, content, created_at)
                        VALUES ('collaboration', :a, :b, :score, :content, NOW())
                    """), {
                        "a": f"{user_a['username']}(id={user_a['id']})",
                        "b": f"{user_b['username']}(id={user_b['id']})",
                        "score": score,
                        "content": json.dumps({"reasoning": reasoning, "ideas": collaboration_ideas}, ensure_ascii=False),
                    })
                return f"Saved to analysis log: {person_a_name} × {person_b_name}"
        except Exception:
            pass
        logger.warning("save_collaboration_recommendation failed: %s", e)
        return f"[WARN] Could not persist to DB ({e})"
