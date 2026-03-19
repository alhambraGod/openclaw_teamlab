"""
邮件摘要 - 编写与发送模块

将科研趋势、团队动态等内容编排为 HTML 邮件，
通过 SMTP 发送给团队成员。
"""

from __future__ import annotations

import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Optional

from config.settings import (
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USER,
    SMTP_PASSWORD,
    SMTP_FROM_NAME,
    SMTP_FROM_EMAIL,
    TEAM_NAME,
)

logger = logging.getLogger(__name__)


async def compose_daily_digest(
    trends: list[dict[str, Any]],
    team_info: Optional[dict[str, Any]] = None,
    custom_message: Optional[str] = None,
) -> str:
    """
    编写每日科研动态摘要的 HTML 邮件内容。

    将论文推荐、团队动态、行动项提醒等整合为
    美观的 HTML 邮件，兼容主流邮件客户端。

    Args:
        trends: 论文推荐列表（来自 research_trend 技能），每项包含：
            - title: str
            - authors: list[str]
            - abstract: str
            - abstract_zh: str (可选)
            - url: str
            - relevance_score: float
        team_info: 团队动态信息（可选），包含：
            - milestones: list[dict] (学生里程碑事件)
            - action_items: list[dict] (待办行动项)
            - announcements: list[str] (通知公告)
        custom_message: 导师自定义消息（可选）

    Returns:
        完整的 HTML 邮件字符串
    """
    today = datetime.now().strftime("%Y年%m月%d日")
    weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = weekday_names[datetime.now().weekday()]

    team_display = getattr(TEAM_NAME, "value", "OpenClaw TeamLab") if hasattr(TEAM_NAME, "value") else str(TEAM_NAME)

    # 构建 HTML
    sections = []

    # --- 问候语 ---
    sections.append(f"""
    <div style="padding: 20px 0; border-bottom: 2px solid #e0e0e0;">
        <h1 style="color: #1a73e8; margin: 0; font-size: 24px;">
            {team_display} 科研日报
        </h1>
        <p style="color: #666; margin: 8px 0 0 0;">
            {today} {weekday}
        </p>
    </div>
    """)

    # --- 导师寄语 ---
    if custom_message:
        sections.append(f"""
        <div style="background: #e8f0fe; padding: 16px; border-radius: 8px; margin: 20px 0;">
            <p style="margin: 0; color: #1a73e8; font-weight: bold;">💬 导师寄语</p>
            <p style="margin: 8px 0 0 0; color: #333;">{custom_message}</p>
        </div>
        """)

    # --- 论文推荐 ---
    if trends:
        paper_cards = []
        for i, paper in enumerate(trends[:10], start=1):
            authors_str = ", ".join(paper.get("authors", [])[:3])
            if len(paper.get("authors", [])) > 3:
                authors_str += " et al."

            abstract_zh = paper.get("abstract_zh", "")
            abstract_section = ""
            if abstract_zh:
                abstract_section = f"""
                <p style="color: #555; font-size: 13px; margin: 8px 0 0 0;">
                    {abstract_zh[:200]}{'...' if len(abstract_zh) > 200 else ''}
                </p>
                """

            relevance = paper.get("relevance_score", 0)
            relevance_color = "#4caf50" if relevance >= 0.7 else "#ff9800" if relevance >= 0.5 else "#9e9e9e"

            paper_cards.append(f"""
            <div style="border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px; margin: 12px 0;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="background: {relevance_color}; color: white; padding: 2px 8px; border-radius: 4px; font-size: 12px;">
                        相关度 {relevance:.0%}
                    </span>
                </div>
                <h3 style="margin: 8px 0; font-size: 15px;">
                    <a href="{paper.get('url', '#')}" style="color: #1a73e8; text-decoration: none;">
                        {paper.get('title', 'Untitled')}
                    </a>
                </h3>
                <p style="color: #888; font-size: 13px; margin: 4px 0;">{authors_str}</p>
                {abstract_section}
            </div>
            """)

        sections.append(f"""
        <div style="margin: 20px 0;">
            <h2 style="color: #333; font-size: 18px; border-bottom: 1px solid #e0e0e0; padding-bottom: 8px;">
                📚 今日论文推荐 ({len(trends[:10])} 篇)
            </h2>
            {''.join(paper_cards)}
        </div>
        """)

    # --- 团队动态 ---
    if team_info:
        dynamic_parts = []

        milestones = team_info.get("milestones", [])
        if milestones:
            ms_items = "".join(
                f"<li style='margin: 4px 0;'>{m.get('student', '')}：{m.get('event', '')}</li>"
                for m in milestones[:5]
            )
            dynamic_parts.append(f"""
            <h3 style="color: #4caf50; font-size: 15px;">🎯 里程碑事件</h3>
            <ul style="padding-left: 20px;">{ms_items}</ul>
            """)

        action_items = team_info.get("action_items", [])
        pending = [ai for ai in action_items if ai.get("status") == "pending"]
        if pending:
            ai_items = "".join(
                f"<li style='margin: 4px 0;'>"
                f"<strong>{ai.get('assignee', '待定')}</strong>：{ai.get('task', '')}"
                f" (截止：{ai.get('deadline', '待定')})</li>"
                for ai in pending[:5]
            )
            dynamic_parts.append(f"""
            <h3 style="color: #ff9800; font-size: 15px;">⏰ 行动项提醒</h3>
            <ul style="padding-left: 20px;">{ai_items}</ul>
            """)

        if dynamic_parts:
            sections.append(f"""
            <div style="margin: 20px 0;">
                <h2 style="color: #333; font-size: 18px; border-bottom: 1px solid #e0e0e0; padding-bottom: 8px;">
                    🏠 团队动态
                </h2>
                {''.join(dynamic_parts)}
            </div>
            """)

    # --- 页脚 ---
    sections.append(f"""
    <div style="border-top: 2px solid #e0e0e0; padding: 16px 0; margin-top: 20px; color: #999; font-size: 12px;">
        <p>本邮件由 {team_display} AI 助手自动生成</p>
        <p>如需调整推送设置，请联系管理员</p>
    </div>
    """)

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 680px; margin: 0 auto; padding: 20px; background: #f5f5f5;">
        <div style="background: white; padding: 24px; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
            {''.join(sections)}
        </div>
    </body>
    </html>
    """
    return html


async def send_digest(
    recipients: list[str],
    subject: str,
    html_body: str,
) -> bool:
    """
    通过 SMTP 发送 HTML 邮件。

    Args:
        recipients: 收件人邮箱列表
        subject: 邮件主题
        html_body: HTML 邮件正文

    Returns:
        True 表示全部发送成功，False 表示有失败

    Raises:
        ValueError: 收件人列表为空
    """
    if not recipients:
        raise ValueError("收件人列表不能为空")

    smtp_host = getattr(SMTP_HOST, "value", str(SMTP_HOST)) if hasattr(SMTP_HOST, "value") else str(SMTP_HOST)
    smtp_port = int(getattr(SMTP_PORT, "value", str(SMTP_PORT)) if hasattr(SMTP_PORT, "value") else str(SMTP_PORT))
    smtp_user = getattr(SMTP_USER, "value", str(SMTP_USER)) if hasattr(SMTP_USER, "value") else str(SMTP_USER)
    smtp_pass = getattr(SMTP_PASSWORD, "value", str(SMTP_PASSWORD)) if hasattr(SMTP_PASSWORD, "value") else str(SMTP_PASSWORD)
    from_name = getattr(SMTP_FROM_NAME, "value", str(SMTP_FROM_NAME)) if hasattr(SMTP_FROM_NAME, "value") else str(SMTP_FROM_NAME)
    from_email = getattr(SMTP_FROM_EMAIL, "value", str(SMTP_FROM_EMAIL)) if hasattr(SMTP_FROM_EMAIL, "value") else str(SMTP_FROM_EMAIL)

    # 构建邮件
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>"

    # 纯文本备选
    plain_text = _html_to_plain(html_body)
    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    all_success = True
    failures: list[str] = []

    try:
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
            server.login(smtp_user, smtp_pass)

            for recipient in recipients:
                try:
                    msg["To"] = recipient
                    server.sendmail(from_email, [recipient], msg.as_string())
                    logger.info("邮件已发送: %s", recipient)
                except smtplib.SMTPRecipientsRefused:
                    logger.warning("收件人被拒绝: %s", recipient)
                    failures.append(recipient)
                    all_success = False
                except smtplib.SMTPException:
                    logger.exception("发送失败: %s", recipient)
                    failures.append(recipient)
                    all_success = False
                finally:
                    # 重置 To 头
                    del msg["To"]

    except smtplib.SMTPAuthenticationError:
        logger.error("SMTP 认证失败，请检查凭据配置")
        return False
    except smtplib.SMTPConnectError:
        logger.error("无法连接到 SMTP 服务器 %s:%d", smtp_host, smtp_port)
        return False
    except Exception:
        logger.exception("邮件发送过程中发生未知错误")
        return False

    if failures:
        logger.warning("以下收件人发送失败: %s", ", ".join(failures))

    logger.info(
        "邮件发送完成: 成功 %d/%d",
        len(recipients) - len(failures),
        len(recipients),
    )
    return all_success


def _html_to_plain(html: str) -> str:
    """将 HTML 邮件转为纯文本备选版本。"""
    import re
    # 移除 HTML 标签
    text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    # 清理空白
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r" {2,}", "\n", text)
    return text.strip()
