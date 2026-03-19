"""
OpenClaw TeamLab — Risk Alert Push
After risk scores are computed, sends proactive alerts to PI via Feishu/DingTalk
for claw_students whose risk level changed to RED or score increased significantly.
"""
from __future__ import annotations

import json
import logging
from sqlalchemy import text
from config.database import get_db

logger = logging.getLogger("teamlab.risk_alerts")


async def send_risk_alerts(risk_results: list[dict]) -> int:
    """Send Feishu/DingTalk alerts for high-risk claw_students.

    Compares current scores against previous computation to detect:
    - New RED-level claw_students
    - Score increase > 15 points

    Returns the number of alerts sent.
    """
    alerts_to_send = []

    for risk in risk_results:
        uid = risk["coevo_user_id"]
        current_score = risk["overall_score"]
        current_level = risk["risk_level"]

        # Get previous score
        prev_score = 0.0
        prev_level = "green"
        try:
            async with get_db() as session:
                result = await session.execute(
                    text("""
                        SELECT overall_score, risk_level FROM claw_student_risk_scores
                        WHERE coevo_user_id = :uid
                        ORDER BY computed_at DESC LIMIT 1 OFFSET 1
                    """),
                    {"uid": uid},
                )
                prev = result.mappings().first()
                if prev:
                    prev_score = float(prev["overall_score"])
                    prev_level = prev["risk_level"]
        except Exception:
            pass

        # Alert conditions
        new_red = (current_level == "red" and prev_level != "red")
        score_jump = (current_score - prev_score) > 15

        if new_red or score_jump:
            alerts_to_send.append({
                "student_name": risk["student_name"],
                "score": current_score,
                "level": current_level,
                "prev_score": prev_score,
                "explanation": risk["explanation"],
                "is_new_red": new_red,
            })

    if not alerts_to_send:
        logger.info("[RiskAlerts] No alerts needed this cycle")
        return 0

    # Build alert message
    lines = [f"**🚨 团队风险预警 — {len(alerts_to_send)}名学生需要关注**\n"]
    for a in alerts_to_send:
        emoji = "🔴" if a["level"] == "red" else "🟡"
        change = f"(↑{a['score'] - a['prev_score']:.0f})" if a["prev_score"] > 0 else "(新)"
        lines.append(
            f"{emoji} **{a['student_name']}** 风险分 {a['score']:.0f} {change}\n"
            f"> {a['explanation']}\n"
        )

    alert_text = "\n".join(lines)

    # Try Feishu first
    sent = 0
    try:
        from feishu import sender as feishu_sender
        from config.settings import settings
        if settings.FEISHU_APP_ID:
            # Send to all known PI open_ids (from claw_pi_config)
            async with get_db() as session:
                result = await session.execute(
                    text("SELECT config_value FROM claw_pi_config WHERE config_key = 'pi_feishu_open_ids'")
                )
                row = result.mappings().first()
                if row and row["config_value"]:
                    open_ids = row["config_value"] if isinstance(row["config_value"], list) else json.loads(row["config_value"])
                    for oid in open_ids:
                        if feishu_sender.send_text(oid, alert_text):
                            sent += 1
    except Exception as e:
        logger.warning("[RiskAlerts] Feishu alert failed: %s", e)

    # Try DingTalk
    try:
        from dingtalk import sender as dt_sender
        from config.settings import settings
        if settings.DINGTALK_CLIENT_ID:
            async with get_db() as session:
                result = await session.execute(
                    text("SELECT config_value FROM claw_pi_config WHERE config_key = 'pi_dingtalk_staff_ids'")
                )
                row = result.mappings().first()
                if row and row["config_value"]:
                    staff_ids = row["config_value"] if isinstance(row["config_value"], list) else json.loads(row["config_value"])
                    for sid in staff_ids:
                        if dt_sender.send_markdown(sid, "🚨 团队风险预警", alert_text):
                            sent += 1
    except Exception as e:
        logger.warning("[RiskAlerts] DingTalk alert failed: %s", e)

    logger.info("[RiskAlerts] Sent %d alerts for %d high-risk claw_students", sent, len(alerts_to_send))
    return sent
