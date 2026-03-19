"""
OpenClaw TeamLab — 统一通知分发器
根据目标渠道（email / feishu / dingtalk / auto）路由推送消息。

使用方式：
    from notify.dispatcher import notify

    await notify(
        channel="email",
        target="user@example.com",
        subject="TeamLab 周报",
        content="...",
    )
"""
from __future__ import annotations

import logging
import re

from notify.email import send_email

logger = logging.getLogger("teamlab.notify")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _is_email(s: str) -> bool:
    return bool(_EMAIL_RE.match(s.strip()))


async def notify(
    channel: str,
    target: str,
    content: str,
    subject: str = "OpenClaw TeamLab 通知",
    html: bool = False,
) -> bool:
    """
    统一通知入口。

    Args:
        channel: "email" | "feishu" | "dingtalk" | "auto"
                 auto 时：若 target 是邮箱地址则发邮件，否则尝试飞书
        target:  邮箱地址 / 飞书 open_id / 钉钉 staff_id
        content: 消息正文
        subject: 邮件主题（仅 email 渠道使用）
        html:    True 时以 HTML 格式发送邮件

    Returns:
        True 表示至少一种渠道发送成功
    """
    channel = channel.strip().lower()

    # auto 通道：按 target 格式自动判断
    if channel == "auto":
        channel = "email" if _is_email(target) else "feishu"

    if channel == "email":
        if not _is_email(target):
            logger.warning("[Notify] Invalid email address: %r", target)
            return False
        return await send_email(target, subject, content, html=html)

    if channel == "feishu":
        return await _send_feishu(target, content)

    if channel == "dingtalk":
        return await _send_dingtalk(target, subject, content)

    logger.warning("[Notify] Unknown channel: %r", channel)
    return False


async def _send_feishu(open_id: str, content: str) -> bool:
    try:
        from feishu import sender as feishu_sender  # type: ignore
        return bool(feishu_sender.send_text(open_id, content))
    except ImportError:
        logger.debug("[Notify] feishu module not available")
    except Exception as exc:
        logger.warning("[Notify] Feishu send failed: %s", exc)
    return False


async def _send_dingtalk(staff_id: str, title: str, content: str) -> bool:
    try:
        from dingtalk import sender as dt_sender  # type: ignore
        return bool(dt_sender.send_markdown(staff_id, title, content))
    except ImportError:
        logger.debug("[Notify] dingtalk module not available")
    except Exception as exc:
        logger.warning("[Notify] DingTalk send failed: %s", exc)
    return False
