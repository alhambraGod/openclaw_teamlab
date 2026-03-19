"""
OpenClaw TeamLab — Memory Manager
分层记忆管理（仿 MemGPT 架构）

记忆层次：
  L0  Redis Working Memory    — 毫秒级，纯内存，任务内共享
  L5  MySQL Session Memory    — 秒级，持久化，跨会话延续

MemGPT 核心思路（适配到我们的场景）：
  - 每个用户有独立的工作记忆（working_facts、active_entities）
  - 对话中识别到的新事实自动写入工作记忆
  - 工作记忆超出容量时自动压缩 → 写入 claw_knowledge_nodes（L2）
  - 用户可 PIN 重要事实（跨会话永久保留）
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from sqlalchemy import text

from config.database import get_db, get_redis
from config.settings import settings

logger = logging.getLogger("teamlab.knowledge.memory")

# Redis key prefix
_REDIS_PREFIX = f"{settings.REDIS_PREFIX}:memory"

# 工作记忆容量（超出后压缩最老的事实）
_WORKING_FACTS_MAX = 20
_ACTIVE_ENTITIES_MAX = 10

# 工作记忆 Redis TTL（秒）—— 用户不活跃超 1 小时后从内存清除（MySQL 仍保留）
_REDIS_SESSION_TTL = 3600


class MemoryManager:
    """
    用户会话记忆管理器。

    用法：
        mm = MemoryManager()
        session = await mm.get_session("web:pi", "web_user")
        await mm.add_fact("web:pi", "张三昨天提交了一篇 ICML 论文")
        await mm.set_active_entities("web:pi", ["张三", "项目A"])
    """

    # ── 会话读写 ──────────────────────────────────────────────────

    async def get_session(self, session_key: str, user_id: str) -> dict:
        """
        获取会话工作记忆（优先 Redis，缺失则从 MySQL 加载并回填 Redis）。
        """
        # 先查 Redis
        redis_data = await self._redis_get(session_key)
        if redis_data:
            return redis_data

        # 从 MySQL 加载
        async with get_db() as db:
            row = (await db.execute(
                text("""
                    SELECT working_facts, active_entities, pinned_facts,
                           persona_notes, turn_count
                    FROM claw_memory_sessions
                    WHERE session_key = :sk
                """),
                {"sk": session_key},
            )).mappings().first()

        if row:
            session = {
                "session_key": session_key,
                "user_id": user_id,
                "working_facts": _parse_json_field(row["working_facts"], []),
                "active_entities": _parse_json_field(row["active_entities"], []),
                "pinned_facts": _parse_json_field(row["pinned_facts"], []),
                "persona_notes": row["persona_notes"] or "",
                "turn_count": row["turn_count"] or 0,
            }
        else:
            session = _empty_session(session_key, user_id)

        # 回填 Redis
        await self._redis_set(session_key, session)
        return session

    async def save_session(self, session_key: str, session: dict) -> None:
        """
        持久化会话到 MySQL + 刷新 Redis 缓存。
        """
        # 截断超长列表
        session["working_facts"] = session.get("working_facts", [])[-_WORKING_FACTS_MAX:]
        session["active_entities"] = session.get("active_entities", [])[-_ACTIVE_ENTITIES_MAX:]

        async with get_db() as db:
            await db.execute(
                text("""
                    INSERT INTO claw_memory_sessions
                        (session_key, user_id, working_facts, active_entities,
                         pinned_facts, persona_notes, turn_count)
                    VALUES
                        (:sk, :uid, :wf, :ae, :pf, :pn, :tc)
                    ON DUPLICATE KEY UPDATE
                        working_facts  = :wf,
                        active_entities = :ae,
                        pinned_facts   = :pf,
                        persona_notes  = :pn,
                        turn_count     = :tc,
                        last_active_at = NOW()
                """),
                {
                    "sk": session_key,
                    "uid": session.get("user_id", "unknown"),
                    "wf": json.dumps(session.get("working_facts", []), ensure_ascii=False),
                    "ae": json.dumps(session.get("active_entities", []), ensure_ascii=False),
                    "pf": json.dumps(session.get("pinned_facts", []), ensure_ascii=False),
                    "pn": session.get("persona_notes", ""),
                    "tc": session.get("turn_count", 0),
                },
            )
            await db.commit()

        await self._redis_set(session_key, session)

    # ── 便捷操作 ──────────────────────────────────────────────────

    async def add_fact(
        self,
        session_key: str,
        fact: str,
        user_id: str = "unknown",
        pin: bool = False,
    ) -> None:
        """
        向工作记忆添加新事实。
        pin=True 时同时写入 pinned_facts（跨会话永久保留）。
        """
        session = await self.get_session(session_key, user_id)

        if fact not in session["working_facts"]:
            session["working_facts"].append(fact)

        if pin and fact not in session["pinned_facts"]:
            session["pinned_facts"].append(fact)

        # 超容时自动蒸馏最老的事实到 claw_knowledge_nodes
        if len(session["working_facts"]) > _WORKING_FACTS_MAX:
            await self._compress_overflow(session_key, session)

        await self.save_session(session_key, session)

    async def set_active_entities(
        self,
        session_key: str,
        entities: list[str],
        user_id: str = "unknown",
    ) -> None:
        """更新当前对话激活的实体列表。"""
        session = await self.get_session(session_key, user_id)
        # 新实体插到列表头，保持最近访问在前
        existing = session.get("active_entities", [])
        merged = entities + [e for e in existing if e not in entities]
        session["active_entities"] = merged[:_ACTIVE_ENTITIES_MAX]
        await self.save_session(session_key, session)

    async def increment_turn(
        self,
        session_key: str,
        user_id: str = "unknown",
    ) -> int:
        """对话轮次 +1，返回最新轮次。"""
        session = await self.get_session(session_key, user_id)
        session["turn_count"] = session.get("turn_count", 0) + 1
        await self.save_session(session_key, session)
        return session["turn_count"]

    async def format_working_memory(
        self,
        session_key: str,
        user_id: str = "unknown",
    ) -> str:
        """
        格式化工作记忆为 LLM system prompt 片段。
        返回 Markdown 文本，空记忆时返回 ""。
        """
        session = await self.get_session(session_key, user_id)
        pinned = session.get("pinned_facts", [])
        working = session.get("working_facts", [])
        entities = session.get("active_entities", [])

        if not pinned and not working and not entities:
            return ""

        lines = ["## 会话工作记忆\n"]
        if entities:
            lines.append(f"**当前活跃实体**: {', '.join(entities)}\n")
        if pinned:
            lines.append("**固定记忆（始终有效）**:")
            for f in pinned:
                lines.append(f"- {f}")
            lines.append("")
        if working:
            lines.append("**工作记忆（本次对话）**:")
            for f in working[-10:]:  # 最近 10 条
                lines.append(f"- {f}")
            lines.append("")

        return "\n".join(lines)

    async def clear_working_memory(
        self,
        session_key: str,
        user_id: str = "unknown",
        keep_pinned: bool = True,
    ) -> None:
        """清除工作记忆（保留 pinned 事实）。"""
        session = await self.get_session(session_key, user_id)
        session["working_facts"] = []
        session["active_entities"] = []
        if not keep_pinned:
            session["pinned_facts"] = []
        await self.save_session(session_key, session)

    # ── Redis 操作 ────────────────────────────────────────────────

    async def _redis_get(self, session_key: str) -> Optional[dict]:
        try:
            r = await get_redis()
            raw = await r.get(f"{_REDIS_PREFIX}:{session_key}")
            if raw:
                return json.loads(raw)
        except Exception as exc:
            logger.debug("Redis get session failed: %s", exc)
        return None

    async def _redis_set(self, session_key: str, session: dict) -> None:
        try:
            r = await get_redis()
            await r.set(
                f"{_REDIS_PREFIX}:{session_key}",
                json.dumps(session, ensure_ascii=False, default=str),
                ex=_REDIS_SESSION_TTL,
            )
        except Exception as exc:
            logger.debug("Redis set session failed: %s", exc)

    # ── 工作记忆压缩（蒸馏溢出事实 → L2 知识节点）────────────────

    async def _compress_overflow(self, session_key: str, session: dict) -> None:
        """
        将溢出的最老事实压缩写入 claw_knowledge_nodes（L2），
        避免工作记忆无限增长。
        """
        try:
            from knowledge.store import KnowledgeStore
            ks = KnowledgeStore()

            overflow = session["working_facts"][:-_WORKING_FACTS_MAX]
            if not overflow:
                return

            user_id = session.get("user_id", "unknown")
            combined = "\n".join(overflow)
            title = f"对话压缩记忆 [{session_key}]"

            await ks.upsert_node(
                entity_type="insight",
                entity_id=user_id,
                title=title,
                content=combined,
                source="user",
                importance=40,
                auto_embed=False,  # 低优先级，不立即嵌入
            )

            # 截断工作记忆至容量上限
            session["working_facts"] = session["working_facts"][-_WORKING_FACTS_MAX:]
            logger.debug("Compressed %d facts to claw_knowledge_nodes for session %s", len(overflow), session_key)
        except Exception as exc:
            logger.warning("_compress_overflow failed: %s", exc)


# ── 工具函数 ────────────────────────────────────────────────────

def _empty_session(session_key: str, user_id: str) -> dict:
    return {
        "session_key": session_key,
        "user_id": user_id,
        "working_facts": [],
        "active_entities": [],
        "pinned_facts": [],
        "persona_notes": "",
        "turn_count": 0,
    }


def _parse_json_field(value, default):
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default
