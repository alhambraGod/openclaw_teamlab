"""
OpenClaw TeamLab — Autonomous Role Base Class

所有自主角色继承此基类，提供：
  - 统一的洞见持久化接口（写入 claw_pi_agent_insights）
  - LLM 调用封装（使用系统全局 LLM 配置）
  - 执行日志记录
"""
from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import openai

from config.settings import settings
from config.database import get_db

logger = logging.getLogger("teamlab.roles")


class AutonomousRole(ABC):
    """自主角色基类。子类需实现 run() 方法。"""

    name: str = "base"
    description: str = "Base autonomous role"

    @abstractmethod
    async def run(self) -> dict[str, Any]:
        """执行角色主逻辑。返回包含 status 和可选 insights 的字典。"""
        ...

    async def llm_call(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 2000,
        temperature: float = 0.3,
    ) -> str:
        """调用 LLM 并返回文本结果。"""
        client = openai.AsyncOpenAI(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY or "no-key",
        )
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = await client.chat.completions.create(
            model=model or settings.LLM_MODEL,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return resp.choices[0].message.content or ""

    async def save_insight(
        self,
        insight_type: str,
        subject: str,
        content: str,
        metadata: dict | None = None,
    ) -> int | None:
        """将生成的洞见持久化到 claw_pi_agent_insights 表。返回主键 id。"""
        try:
            from sqlalchemy import text
            async with get_db() as db:
                result = await db.execute(
                    text("""
                        INSERT INTO claw_pi_agent_insights
                            (insight_type, subject, content, metadata, created_at)
                        VALUES (:type, :subject, :content, :meta, NOW())
                    """),
                    {
                        "type": insight_type,
                        "subject": subject,
                        "content": content,
                        "meta": json.dumps(metadata, ensure_ascii=False) if metadata else None,
                    },
                )
                return result.lastrowid
        except Exception as exc:
            logger.error("[%s] Failed to save insight: %s", self.name, exc)
            return None

    async def get_recent_insights(
        self,
        insight_type: str,
        days: int = 7,
        limit: int = 20,
    ) -> list[dict]:
        """从 claw_pi_agent_insights 检索近期洞见。"""
        try:
            from sqlalchemy import text
            async with get_db() as db:
                rows = (await db.execute(
                    text("""
                        SELECT id, insight_type, subject, content, metadata, created_at
                        FROM claw_pi_agent_insights
                        WHERE insight_type = :type
                          AND created_at >= DATE_SUB(NOW(), INTERVAL :days DAY)
                        ORDER BY created_at DESC
                        LIMIT :lim
                    """),
                    {"type": insight_type, "days": days, "lim": limit},
                )).mappings().all()
                return [dict(r) for r in rows]
        except Exception as exc:
            logger.error("[%s] Failed to load insights: %s", self.name, exc)
            return []
