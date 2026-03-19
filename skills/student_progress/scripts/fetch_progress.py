"""
学生科研进展追踪 - 数据获取模块

从 MySQL 数据库查询学生档案、能力评分、成长趋势和里程碑事件，
为雷达图可视化和趋势分析提供结构化数据。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

import aiomysql

from config.database import get_db_pool
from config.settings import CAPABILITY_DIMENSIONS

logger = logging.getLogger(__name__)

# 七个核心能力维度（与 settings 中保持一致）
DEFAULT_DIMENSIONS = [
    "论文阅读",
    "实验设计",
    "代码实现",
    "学术写作",
    "团队协作",
    "创新思维",
    "汇报表达",
]


async def fetch_student_profile(student_id: str) -> dict[str, Any]:
    """
    获取学生的完整档案信息，包括基本信息、最新评分和近期事件。

    Args:
        student_id: 学生唯一标识符

    Returns:
        包含学生档案的字典，结构如下：
        {
            "id": str,
            "name": str,
            "grade": str,
            "research_area": str,
            "advisor": str,
            "enrollment_date": str,
            "latest_scores": dict[str, float],
            "recent_events": list[dict],
        }

    Raises:
        ValueError: 如果 student_id 为空或学生不存在
    """
    if not student_id:
        raise ValueError("student_id 不能为空")

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            # 查询学生基本信息
            await cur.execute(
                """
                SELECT id, name, grade, research_area, advisor, enrollment_date
                FROM claw_students
                WHERE id = %s
                """,
                (student_id,),
            )
            student = await cur.fetchone()

            if not student:
                raise ValueError(f"未找到学生: {student_id}")

            # 查询最新能力评分
            await cur.execute(
                """
                SELECT dimension, score, assessed_at
                FROM claw_capability_scores
                WHERE student_id = %s
                  AND assessed_at = (
                      SELECT MAX(assessed_at)
                      FROM claw_capability_scores
                      WHERE student_id = %s AND dimension = claw_capability_scores.dimension
                  )
                ORDER BY dimension
                """,
                (student_id, student_id),
            )
            score_rows = await cur.fetchall()
            latest_scores = {row["dimension"]: row["score"] for row in score_rows}

            # 查询近期事件（最近30天）
            thirty_days_ago = datetime.now() - timedelta(days=30)
            await cur.execute(
                """
                SELECT event_type, description, dimension, event_date
                FROM student_events
                WHERE student_id = %s AND event_date >= %s
                ORDER BY event_date DESC
                LIMIT 20
                """,
                (student_id, thirty_days_ago),
            )
            recent_events = await cur.fetchall()

    profile = {
        "id": student["id"],
        "name": student["name"],
        "grade": student["grade"],
        "research_area": student["research_area"],
        "advisor": student["advisor"],
        "enrollment_date": str(student["enrollment_date"]),
        "latest_scores": latest_scores,
        "recent_events": [
            {
                "event_type": evt["event_type"],
                "description": evt["description"],
                "dimension": evt["dimension"],
                "event_date": str(evt["event_date"]),
            }
            for evt in recent_events
        ],
    }

    logger.info("已获取学生档案: %s (%s)", student["name"], student_id)
    return profile


async def fetch_radar_data(student_id: str) -> dict[str, Any]:
    """
    获取学生各能力维度的最新评分，用于生成雷达图。
    同时获取上一评估周期的评分用于对比。

    Args:
        student_id: 学生唯一标识符

    Returns:
        雷达图数据字典：
        {
            "dimensions": list[str],
            "current_scores": list[float],
            "previous_scores": list[float],
            "assessed_at": str,
            "previous_assessed_at": str | None,
        }
    """
    if not student_id:
        raise ValueError("student_id 不能为空")

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            # 获取最新评估周期
            await cur.execute(
                """
                SELECT DISTINCT assessed_at
                FROM claw_capability_scores
                WHERE student_id = %s
                ORDER BY assessed_at DESC
                LIMIT 2
                """,
                (student_id,),
            )
            periods = await cur.fetchall()

            current_scores: dict[str, float] = {}
            previous_scores: dict[str, float] = {}
            assessed_at: Optional[str] = None
            previous_assessed_at: Optional[str] = None

            if periods:
                # 最新一期评分
                assessed_at = str(periods[0]["assessed_at"])
                await cur.execute(
                    """
                    SELECT dimension, score
                    FROM claw_capability_scores
                    WHERE student_id = %s AND assessed_at = %s
                    """,
                    (student_id, periods[0]["assessed_at"]),
                )
                for row in await cur.fetchall():
                    current_scores[row["dimension"]] = row["score"]

            if len(periods) > 1:
                # 上一期评分
                previous_assessed_at = str(periods[1]["assessed_at"])
                await cur.execute(
                    """
                    SELECT dimension, score
                    FROM claw_capability_scores
                    WHERE student_id = %s AND assessed_at = %s
                    """,
                    (student_id, periods[1]["assessed_at"]),
                )
                for row in await cur.fetchall():
                    previous_scores[row["dimension"]] = row["score"]

    dimensions = getattr(CAPABILITY_DIMENSIONS, "list", DEFAULT_DIMENSIONS)

    return {
        "dimensions": dimensions,
        "current_scores": [current_scores.get(d, None) for d in dimensions],
        "previous_scores": [previous_scores.get(d, None) for d in dimensions],
        "assessed_at": assessed_at,
        "previous_assessed_at": previous_assessed_at,
    }


async def fetch_growth_trend(
    student_id: str, months: int = 6
) -> list[dict[str, Any]]:
    """
    获取学生在指定时间范围内各维度评分的变化趋势。

    Args:
        student_id: 学生唯一标识符
        months: 回溯月数，默认6个月

    Returns:
        按月排列的趋势数据列表：
        [
            {
                "month": "2025-10",
                "avg_score": 68.5,
                "dimension_scores": {"论文阅读": 70, ...},
            },
            ...
        ]
    """
    if not student_id:
        raise ValueError("student_id 不能为空")
    if months < 1:
        raise ValueError("months 必须大于0")

    pool = await get_db_pool()
    start_date = datetime.now() - timedelta(days=months * 30)

    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """
                SELECT
                    DATE_FORMAT(assessed_at, '%%Y-%%m') AS month,
                    dimension,
                    AVG(score) AS avg_score
                FROM claw_capability_scores
                WHERE student_id = %s AND assessed_at >= %s
                GROUP BY month, dimension
                ORDER BY month ASC, dimension ASC
                """,
                (student_id, start_date),
            )
            rows = await cur.fetchall()

    # 按月聚合
    monthly_data: dict[str, dict[str, float]] = {}
    for row in rows:
        month = row["month"]
        if month not in monthly_data:
            monthly_data[month] = {}
        monthly_data[month][row["dimension"]] = float(row["avg_score"])

    trend = []
    for month in sorted(monthly_data.keys()):
        scores = monthly_data[month]
        valid_scores = [v for v in scores.values() if v is not None]
        avg = sum(valid_scores) / len(valid_scores) if valid_scores else 0.0
        trend.append(
            {
                "month": month,
                "avg_score": round(avg, 2),
                "dimension_scores": scores,
            }
        )

    logger.info(
        "已获取学生 %s 近 %d 个月的成长趋势（%d 个数据点）",
        student_id,
        months,
        len(trend),
    )
    return trend


async def update_capability_score(
    student_id: str,
    dimension: str,
    score: float,
    evidence: str,
) -> bool:
    """
    更新学生某一能力维度的评分。

    Args:
        student_id: 学生唯一标识符
        dimension: 能力维度名称（必须是七个核心维度之一）
        score: 评分（0-100）
        evidence: 评分依据说明

    Returns:
        True 表示更新成功，False 表示失败

    Raises:
        ValueError: 参数校验失败
    """
    valid_dimensions = getattr(CAPABILITY_DIMENSIONS, "list", DEFAULT_DIMENSIONS)
    if dimension not in valid_dimensions:
        raise ValueError(
            f"无效的能力维度: {dimension}。有效维度: {valid_dimensions}"
        )
    if not (0 <= score <= 100):
        raise ValueError(f"评分必须在 0-100 之间，当前值: {score}")
    if not evidence.strip():
        raise ValueError("评分依据不能为空")

    pool = await get_db_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO claw_capability_scores
                        (student_id, dimension, score, evidence, assessed_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    """,
                    (student_id, dimension, score, evidence),
                )
            await conn.commit()

        logger.info(
            "已更新学生 %s 的 %s 评分: %.1f",
            student_id,
            dimension,
            score,
        )
        return True

    except Exception:
        logger.exception("更新能力评分失败: student=%s, dim=%s", student_id, dimension)
        return False
