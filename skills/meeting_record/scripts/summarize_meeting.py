"""
会议记录 - 智能摘要与行动项提取模块

将原始会议笔记整理为结构化摘要，
自动提取行动项、负责人和截止日期。
支持 LLM 生成和规则提取两种模式。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Optional

import aiomysql
from openai import AsyncOpenAI

from config.database import get_db_pool
from config.settings import settings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    """获取或创建 LLM 客户端单例。"""
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            base_url=settings.LLM_BASE_URL, api_key=settings.LLM_API_KEY
        )
    return _client


async def summarize_notes(raw_notes: str) -> dict[str, Any]:
    """
    对原始会议笔记生成结构化摘要。

    结合 LLM 语义理解和规则提取，将非结构化的会议笔记
    整理为包含主题、讨论要点、决策和开放问题的结构化格式。

    Args:
        raw_notes: 原始会议笔记文本（可以是语音转录或手动记录）

    Returns:
        结构化摘要字典：
        {
            "title": str,
            "summary": str,
            "key_decisions": list[str],
            "topics": list[str],
            "next_steps": list[str],
            "discussion_points": list[dict],
            "extracted_topics": list[str],
            "word_count": int,
        }

    Raises:
        ValueError: 如果 raw_notes 为空
    """
    if not raw_notes or not raw_notes.strip():
        raise ValueError("会议笔记不能为空")

    # 使用 LLM 生成结构化摘要
    client = _get_client()
    resp = await client.chat.completions.create(
        model=settings.LLM_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是一个学术会议记录助手。请对以下会议记录进行结构化总结。\n"
                    "输出JSON格式：\n"
                    "{\n"
                    '  "title": "会议标题（10字以内）",\n'
                    '  "summary": "300字以内的会议摘要",\n'
                    '  "key_decisions": ["决策1", "决策2"],\n'
                    '  "topics": ["讨论主题1", "讨论主题2"],\n'
                    '  "next_steps": ["下一步计划1"],\n'
                    '  "discussion_points": [\n'
                    "    {\n"
                    '      "topic": "主题名",\n'
                    '      "key_points": ["要点1", "要点2"],\n'
                    '      "decisions": ["决定"],\n'
                    '      "open_questions": ["待解决的问题"]\n'
                    "    }\n"
                    "  ]\n"
                    "}"
                ),
            },
            {"role": "user", "content": raw_notes},
        ],
        max_tokens=2000,
    )

    try:
        result = json.loads(resp.choices[0].message.content)
    except (json.JSONDecodeError, IndexError):
        # 降级：LLM 返回非 JSON 时使用纯文本
        result = {
            "title": "团队会议",
            "summary": resp.choices[0].message.content,
            "key_decisions": [],
            "topics": [],
            "next_steps": [],
            "discussion_points": [],
        }

    # 补充规则提取的研究关键词
    result["extracted_topics"] = _extract_research_keywords(raw_notes)
    result["word_count"] = len(raw_notes)

    # 确保标题存在
    if not result.get("title"):
        topics = result.get("topics", [])
        result["title"] = (
            f"关于{topics[0]}等议题的讨论" if topics else "团队会议"
        )

    # 持久化到数据库
    await _save_meeting_summary(result, raw_notes)

    logger.info(
        "已生成会议摘要: %s (%d 个议题)",
        result.get("title", ""),
        len(result.get("discussion_points", [])),
    )
    return result


async def extract_action_items(
    notes: str,
    participants: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """
    从会议笔记中提取行动项（Action Items）。

    结合 LLM 语义理解和规则匹配，识别任务分配、
    负责人和截止日期。

    Args:
        notes: 会议笔记文本
        participants: 参会人员列表，用于辅助验证负责人

    Returns:
        行动项列表：
        [
            {
                "id": "AI-001",
                "assignee": str,
                "task": str,
                "deadline": str,
                "priority": "high" | "medium" | "low",
                "source_text": str,
            }
        ]
    """
    if not notes or not notes.strip():
        return []

    # 使用 LLM 提取行动项
    client = _get_client()
    resp = await client.chat.completions.create(
        model=settings.LLM_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "从会议记录中提取所有行动项(action items)。\n"
                    "输出JSON数组：\n"
                    "[\n"
                    "  {\n"
                    '    "assignee": "负责人姓名",\n'
                    '    "task": "具体任务描述",\n'
                    '    "deadline": "截止时间（如无明确时间填待定）",\n'
                    '    "priority": "high/medium/low",\n'
                    '    "source_text": "原文中对应的句子"\n'
                    "  }\n"
                    "]"
                ),
            },
            {"role": "user", "content": notes},
        ],
        max_tokens=1000,
    )

    try:
        raw_items = json.loads(resp.choices[0].message.content)
    except (json.JSONDecodeError, IndexError):
        raw_items = []

    # 规则补充提取（LLM 可能遗漏的）
    rule_items = _rule_based_extract(notes, participants)

    # 合并去重
    all_items = _merge_action_items(raw_items, rule_items)

    # 添加 ID 并验证
    action_items = []
    for i, item in enumerate(all_items, start=1):
        assignee = item.get("assignee", "待定")

        # 验证负责人是否在参会人员中
        if participants and assignee != "待定":
            if not any(assignee in p for p in participants):
                assignee = f"{assignee}（待确认）"

        action_items.append(
            {
                "id": f"AI-{i:03d}",
                "assignee": assignee,
                "task": item.get("task", ""),
                "deadline": item.get("deadline", "待定"),
                "priority": item.get("priority", "medium"),
                "source_text": item.get("source_text", ""),
            }
        )

    # 持久化
    if action_items:
        await _save_action_items(action_items)

    logger.info("提取了 %d 个行动项", len(action_items))
    return action_items


# ============================================================
# 内部辅助函数
# ============================================================


def _extract_research_keywords(text: str) -> list[str]:
    """提取研究领域关键词（用于方向发现技能）。"""
    # 英文技术术语
    en_terms = re.findall(r"\b[A-Z][a-zA-Z]*(?:\s+[A-Z][a-zA-Z]*)+\b", text)
    # 中文技术术语
    tech_patterns = [
        r"[\u4e00-\u9fff]*(?:学习|网络|模型|算法|系统|架构|框架)[\u4e00-\u9fff]*"
    ]
    zh_terms = []
    for pattern in tech_patterns:
        zh_terms.extend(re.findall(pattern, text))

    all_terms = list(set(en_terms + zh_terms))
    return all_terms[:10]


def _rule_based_extract(
    notes: str, participants: Optional[list[str]] = None
) -> list[dict[str, str]]:
    """基于规则的行动项提取（补充 LLM 结果）。"""
    items = []
    sentences = re.split(r"[。；\n]", notes)

    action_patterns = [
        r"(?P<assignee>[\u4e00-\u9fff]{2,4})负责(?P<task>.+)",
        r"请(?P<assignee>[\u4e00-\u9fff]{2,4})(?P<task>.+)",
        r"(?:TODO|待办|行动项)[：:]?\s*(?P<task>.+)",
    ]

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence or len(sentence) < 5:
            continue
        for pattern in action_patterns:
            match = re.search(pattern, sentence)
            if match:
                groups = match.groupdict()
                items.append(
                    {
                        "assignee": groups.get("assignee", "待定"),
                        "task": groups.get("task", sentence).strip(),
                        "deadline": "待定",
                        "priority": _assess_priority(sentence),
                        "source_text": sentence,
                    }
                )
                break

    return items


def _merge_action_items(
    llm_items: list[dict], rule_items: list[dict]
) -> list[dict]:
    """合并 LLM 和规则提取的行动项，去重。"""
    merged = list(llm_items)
    existing_tasks = {item.get("task", "").lower() for item in merged}

    for item in rule_items:
        task_lower = item.get("task", "").lower()
        # 简单去重：如果任务描述高度相似则跳过
        if task_lower and not any(
            task_lower in existing or existing in task_lower
            for existing in existing_tasks
            if existing
        ):
            merged.append(item)
            existing_tasks.add(task_lower)

    return merged


def _assess_priority(sentence: str) -> str:
    """根据句子内容评估优先级。"""
    high_kw = ["紧急", "尽快", "立刻", "马上", "重要", "优先", "deadline"]
    low_kw = ["有空", "方便时", "不急", "以后", "可选"]

    for kw in high_kw:
        if kw in sentence.lower():
            return "high"
    for kw in low_kw:
        if kw in sentence.lower():
            return "low"
    return "medium"


async def _save_meeting_summary(
    summary: dict[str, Any], raw_notes: str
) -> None:
    """保存会议摘要到数据库。"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO claw_meetings
                    (title, content, summary_json, topics_json, meeting_date)
                VALUES (%s, %s, %s, %s, NOW())
                """,
                (
                    summary.get("title", "团队会议"),
                    raw_notes,
                    json.dumps(summary, ensure_ascii=False),
                    json.dumps(
                        summary.get("extracted_topics", []),
                        ensure_ascii=False,
                    ),
                ),
            )
        await conn.commit()


async def _save_action_items(items: list[dict[str, Any]]) -> None:
    """保存行动项到数据库。"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for item in items:
                await cur.execute(
                    """
                    INSERT INTO action_items
                        (task_id, task, assignee, deadline, priority, status, created_at)
                    VALUES (%s, %s, %s, %s, %s, 'pending', NOW())
                    """,
                    (
                        item["id"],
                        item["task"],
                        item.get("assignee"),
                        item.get("deadline"),
                        item["priority"],
                    ),
                )
        await conn.commit()
