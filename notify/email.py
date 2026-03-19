"""
OpenClaw TeamLab — Async Email Sender
使用 aiosmtplib 通过 SMTP over SSL（465）发送邮件。
支持纯文本和 HTML 两种格式，自动重试一次。
"""
from __future__ import annotations

import asyncio
import logging
from email.headerregistry import Address
from email.message import EmailMessage

import aiosmtplib

from config.settings import settings

logger = logging.getLogger("teamlab.email")


def _build_message(
    to: str | list[str],
    subject: str,
    body: str,
    html: bool = False,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = Address(
        display_name="OpenClaw TeamLab",
        addr_spec=settings.SMTP_FROM or settings.SMTP_USER,
    )
    recipients = [to] if isinstance(to, str) else to
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject

    if html:
        msg.set_content(body, subtype="html", charset="utf-8")
    else:
        msg.set_content(body, charset="utf-8")
    return msg


async def send_email(
    to: str | list[str],
    subject: str,
    body: str,
    html: bool = False,
    *,
    retry: int = 1,
) -> bool:
    """
    发送邮件。

    Args:
        to: 收件人地址（单个字符串或列表）
        subject: 邮件主题
        body: 邮件正文（纯文本或 HTML）
        html: True 时以 text/html 格式发送
        retry: 失败后重试次数（默认 1 次）

    Returns:
        True 表示发送成功，False 表示失败
    """
    if not settings.SMTP_USER or not settings.SMTP_AUTH_CODE:
        logger.warning("[Email] SMTP_USER or SMTP_AUTH_CODE not configured — skipping")
        return False

    msg = _build_message(to, subject, body, html=html)
    host = settings.SMTP_HOST
    port = settings.SMTP_PORT
    use_tls = settings.SMTP_USE_TLS

    for attempt in range(retry + 1):
        try:
            await aiosmtplib.send(
                msg,
                hostname=host,
                port=port,
                use_tls=use_tls,
                timeout=20,
            )
            recipients = [to] if isinstance(to, str) else to
            logger.info(
                "[Email] Sent to %s | subject=%r (attempt=%d)",
                ", ".join(recipients), subject, attempt + 1,
            )
            return True
        except Exception as exc:
            logger.warning(
                "[Email] Send failed (attempt=%d/%d): %s", attempt + 1, retry + 1, exc
            )
            if attempt < retry:
                await asyncio.sleep(2)

    return False


async def send_task_result_email(
    to: str,
    task_id: str,
    question: str,
    result: str,
) -> bool:
    """任务完成时发送结果邮件（纯文本 Markdown 友好格式）。"""
    subject = f"✅ TeamLab 任务完成：{question[:40]}{'…' if len(question) > 40 else ''}"
    body = (
        f"您好，\n\n"
        f"您提交的 TeamLab 任务已完成。\n\n"
        f"**问题：** {question}\n\n"
        f"**结果：**\n\n{result}\n\n"
        f"---\n任务 ID：{task_id}\n"
        f"此邮件由 OpenClaw TeamLab 自动发送，请勿直接回复。\n"
    )
    return await send_email(to, subject, body)


async def send_task_timeout_email(
    to: str,
    task_id: str,
    question: str,
) -> bool:
    """任务超时时发送友好通知邮件。"""
    subject = f"⏳ TeamLab 任务超时提醒：{question[:40]}{'…' if len(question) > 40 else ''}"
    body = (
        f"您好，\n\n"
        f"您提交的 TeamLab 任务执行时间超过了 3 分钟限制，已被自动终止。\n\n"
        f"**问题：** {question}\n\n"
        f"**建议操作：**\n"
        f"1. 问题可能较为复杂，请尝试将其拆解为更小的子问题后分别提交\n"
        f"2. 等待片刻（数据缓存后会更快），然后重新提交\n"
        f"3. 如持续超时请联系管理员\n\n"
        f"---\n任务 ID：{task_id}\n"
        f"此邮件由 OpenClaw TeamLab 自动发送，请勿直接回复。\n"
    )
    return await send_email(to, subject, body)


async def send_proactive_report_email(
    to: str | list[str],
    subject: str,
    content: str,
) -> bool:
    """系统主动推送（周报、风险预警、研究动态等）邮件。"""
    return await send_email(to, subject, content)
