"""
科研协作推荐 - 匹配算法模块

基于学生能力矩阵计算互补性得分，生成最优协作推荐。
使用余弦相似度的逆指标衡量互补性，结合方向契合度和覆盖完整度。
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from itertools import combinations
from typing import Any, Optional

import aiomysql

from config.database import get_db_pool
from config.settings import CAPABILITY_DIMENSIONS

logger = logging.getLogger(__name__)

DEFAULT_DIMENSIONS = [
    "论文阅读",
    "实验设计",
    "代码实现",
    "学术写作",
    "团队协作",
    "创新思维",
    "汇报表达",
]

# 能力得分阈值：高于此值认为是"强项"
STRENGTH_THRESHOLD = 75.0
# 能力得分阈值：低于此值认为是"弱项"
WEAKNESS_THRESHOLD = 55.0


async def build_skill_matrix(
    claw_students: Optional[list[str]] = None,
) -> dict[str, dict[str, float]]:
    """
    构建学生能力矩阵。

    从数据库拉取所有（或指定）学生的最新能力评分，
    构建 {student_id: {dimension: score}} 的嵌套字典。

    Args:
        claw_students: 可选的学生ID列表。为 None 时查询所有活跃学生。

    Returns:
        能力矩阵字典，键为学生ID，值为 {维度: 得分} 字典。
        缺失的维度不包含在内。

    Example:
        >>> matrix = await build_skill_matrix(["s001", "s002"])
        >>> matrix["s001"]["代码实现"]
        92.0
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            if claw_students:
                placeholders = ", ".join(["%s"] * len(claw_students))
                query = f"""
                    SELECT cs.student_id, cs.dimension, cs.score
                    FROM claw_capability_scores cs
                    INNER JOIN (
                        SELECT student_id, dimension, MAX(assessed_at) AS latest
                        FROM claw_capability_scores
                        WHERE student_id IN ({placeholders})
                        GROUP BY student_id, dimension
                    ) latest_scores
                    ON cs.student_id = latest_scores.student_id
                       AND cs.dimension = latest_scores.dimension
                       AND cs.assessed_at = latest_scores.latest
                """
                await cur.execute(query, tuple(claw_students))
            else:
                await cur.execute(
                    """
                    SELECT cs.student_id, cs.dimension, cs.score
                    FROM claw_capability_scores cs
                    INNER JOIN (
                        SELECT student_id, dimension, MAX(assessed_at) AS latest
                        FROM claw_capability_scores
                        GROUP BY student_id, dimension
                    ) latest_scores
                    ON cs.student_id = latest_scores.student_id
                       AND cs.dimension = latest_scores.dimension
                       AND cs.assessed_at = latest_scores.latest
                    INNER JOIN claw_students s ON cs.student_id = s.id
                    WHERE s.is_active = 1
                    """
                )
            rows = await cur.fetchall()

    matrix: dict[str, dict[str, float]] = {}
    for row in rows:
        sid = row["student_id"]
        if sid not in matrix:
            matrix[sid] = {}
        matrix[sid][row["dimension"]] = float(row["score"])

    logger.info("已构建能力矩阵: %d 名学生", len(matrix))
    return matrix


def calculate_complementarity(
    student_a: dict[str, float],
    student_b: dict[str, float],
) -> float:
    """
    计算两位学生的能力互补性得分。

    互补性得分综合考虑以下因素：
    1. 弱项补足度 (40%): A的强项能否覆盖B的弱项，反之亦然
    2. 技能差异性 (30%): 两人能力分布的差异程度
    3. 组合覆盖度 (30%): 合并后能覆盖多少维度达到"优秀"水平

    Args:
        student_a: 学生A的能力字典 {维度: 得分}
        student_b: 学生B的能力字典 {维度: 得分}

    Returns:
        互补性得分，范围 [0.0, 1.0]，越高表示越互补。
    """
    dimensions = getattr(CAPABILITY_DIMENSIONS, "list", DEFAULT_DIMENSIONS)

    # --- 1. 弱项补足度 ---
    complement_score = 0.0
    complement_count = 0

    for dim in dimensions:
        sa = student_a.get(dim)
        sb = student_b.get(dim)
        if sa is None or sb is None:
            continue

        # A强B弱
        if sa >= STRENGTH_THRESHOLD and sb < WEAKNESS_THRESHOLD:
            complement_score += (sa - sb) / 100.0
            complement_count += 1
        # B强A弱
        elif sb >= STRENGTH_THRESHOLD and sa < WEAKNESS_THRESHOLD:
            complement_score += (sb - sa) / 100.0
            complement_count += 1

    weakness_coverage = (
        complement_score / max(complement_count, 1)
        if complement_count > 0
        else 0.0
    )

    # --- 2. 技能差异性（向量距离归一化） ---
    diff_sum = 0.0
    valid_dims = 0
    for dim in dimensions:
        sa = student_a.get(dim)
        sb = student_b.get(dim)
        if sa is not None and sb is not None:
            diff_sum += abs(sa - sb)
            valid_dims += 1

    diversity = (diff_sum / (valid_dims * 100.0)) if valid_dims > 0 else 0.0

    # --- 3. 组合覆盖度 ---
    covered = 0
    for dim in dimensions:
        sa = student_a.get(dim, 0.0)
        sb = student_b.get(dim, 0.0)
        best = max(sa, sb)
        if best >= STRENGTH_THRESHOLD:
            covered += 1

    coverage = covered / len(dimensions)

    # 综合得分
    final_score = (
        0.4 * min(weakness_coverage, 1.0)
        + 0.3 * diversity
        + 0.3 * coverage
    )

    return round(min(final_score, 1.0), 4)


async def generate_recommendations(
    claw_students: Optional[list[str]] = None,
    top_k: int = 5,
    required_skills: Optional[list[str]] = None,
    exclude_pairs: Optional[list[tuple[str, str]]] = None,
) -> list[dict[str, Any]]:
    """
    生成排名前 top_k 的协作推荐组合。

    Args:
        claw_students: 候选学生ID列表，为 None 时考虑所有活跃学生
        top_k: 返回推荐组数
        required_skills: 组合必须覆盖的能力维度
        exclude_pairs: 需排除的学生对列表

    Returns:
        推荐列表，按互补性得分降序排列：
        [
            {
                "rank": int,
                "student_ids": [str, str],
                "complementarity_score": float,
                "strengths": {student_id: [str, ...]},
                "combined_coverage": [str, ...],
            },
            ...
        ]
    """
    exclude_set = set()
    if exclude_pairs:
        for pair in exclude_pairs:
            exclude_set.add(tuple(sorted(pair)))

    matrix = await build_skill_matrix(claw_students)

    if len(matrix) < 2:
        logger.warning("候选学生不足2人，无法生成推荐")
        return []

    dimensions = getattr(CAPABILITY_DIMENSIONS, "list", DEFAULT_DIMENSIONS)

    scored_pairs: list[tuple[float, str, str]] = []

    for sid_a, sid_b in combinations(matrix.keys(), 2):
        pair_key = tuple(sorted([sid_a, sid_b]))
        if pair_key in exclude_set:
            continue

        score = calculate_complementarity(matrix[sid_a], matrix[sid_b])

        # 检查必需技能覆盖
        if required_skills:
            covered = True
            for skill in required_skills:
                best = max(
                    matrix[sid_a].get(skill, 0.0),
                    matrix[sid_b].get(skill, 0.0),
                )
                if best < STRENGTH_THRESHOLD:
                    covered = False
                    break
            if not covered:
                continue

        scored_pairs.append((score, sid_a, sid_b))

    # 按得分降序排序
    scored_pairs.sort(key=lambda x: x[0], reverse=True)

    recommendations = []
    for rank, (score, sid_a, sid_b) in enumerate(scored_pairs[:top_k], start=1):
        strengths_a = [
            d
            for d in dimensions
            if matrix[sid_a].get(d, 0) >= STRENGTH_THRESHOLD
        ]
        strengths_b = [
            d
            for d in dimensions
            if matrix[sid_b].get(d, 0) >= STRENGTH_THRESHOLD
        ]

        combined = set()
        for d in dimensions:
            best = max(matrix[sid_a].get(d, 0.0), matrix[sid_b].get(d, 0.0))
            if best >= STRENGTH_THRESHOLD:
                combined.add(d)

        recommendations.append(
            {
                "rank": rank,
                "student_ids": [sid_a, sid_b],
                "complementarity_score": score,
                "strengths": {sid_a: strengths_a, sid_b: strengths_b},
                "combined_coverage": sorted(combined),
            }
        )

    logger.info(
        "已生成 %d 组协作推荐（共评估 %d 对）",
        len(recommendations),
        len(scored_pairs),
    )
    return recommendations


async def save_recommendations(
    recommendations: list[dict[str, Any]],
) -> None:
    """
    将协作推荐结果保存到数据库。

    Args:
        recommendations: generate_recommendations 返回的推荐列表
    """
    if not recommendations:
        logger.info("无推荐结果需要保存")
        return

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for rec in recommendations:
                student_ids = rec["student_ids"]
                await cur.execute(
                    """
                    INSERT INTO claw_collaboration_recommendations
                        (student_a_id, student_b_id, complementarity_score,
                         combined_coverage, created_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    """,
                    (
                        student_ids[0],
                        student_ids[1],
                        rec["complementarity_score"],
                        ",".join(rec["combined_coverage"]),
                    ),
                )
        await conn.commit()

    logger.info("已保存 %d 条协作推荐到数据库", len(recommendations))
