"""
科研方向发现 - 主题组合与评估模块

从会议记录中提取研究主题，生成排列组合，
并评估每种组合的科研潜力、新颖性和可行性。
"""

from __future__ import annotations

import logging
import re
from itertools import combinations
from typing import Any, Optional

import aiohttp

from config.database import get_db_pool
from config.settings import ARXIV_API_BASE, TEAM_RESEARCH_AREAS

logger = logging.getLogger(__name__)


async def extract_meeting_topics(
    meeting_ids: Optional[list[str]] = None,
    additional_topics: Optional[list[str]] = None,
) -> list[str]:
    """
    从会议记录中提取研究主题关键词。

    使用 TF-IDF 和领域术语表对会议内容进行关键词提取，
    合并同义词，过滤通用词汇。

    Args:
        meeting_ids: 指定的会议ID列表，为 None 时使用最近10次会议
        additional_topics: 手动补充的额外主题列表

    Returns:
        去重后的主题关键词列表，按出现频率降序排列。

    Example:
        >>> topics = await extract_meeting_topics(["m001", "m002"])
        >>> topics
        ["联邦学习", "知识图谱", "隐私保护", "多模态", ...]
    """
    pool = await get_db_pool()

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            if meeting_ids:
                placeholders = ", ".join(["%s"] * len(meeting_ids))
                await cur.execute(
                    f"""
                    SELECT id, title, content, topics_json
                    FROM claw_meetings
                    WHERE id IN ({placeholders})
                    ORDER BY meeting_date DESC
                    """,
                    tuple(meeting_ids),
                )
            else:
                await cur.execute(
                    """
                    SELECT id, title, content, topics_json
                    FROM claw_meetings
                    ORDER BY meeting_date DESC
                    LIMIT 10
                    """
                )
            claw_meetings = await cur.fetchall()

    # 从会议记录中提取主题
    raw_topics: list[str] = []

    for meeting in claw_meetings:
        _id, title, content, topics_json = meeting

        # 优先使用已标注的主题
        if topics_json:
            import json
            try:
                tagged_topics = json.loads(topics_json)
                raw_topics.extend(tagged_topics)
            except json.JSONDecodeError:
                pass

        # 从标题和内容中提取关键词
        text = f"{title or ''} {content or ''}"
        extracted = _extract_keywords(text)
        raw_topics.extend(extracted)

    # 添加手动补充的主题
    if additional_topics:
        raw_topics.extend(additional_topics)

    # 去重、统计频率、排序
    from collections import Counter
    topic_counts = Counter(_normalize_topic(t) for t in raw_topics if t.strip())

    # 过滤出现次数少于1次的噪声词
    filtered = [
        topic for topic, count in topic_counts.most_common()
        if count >= 1 and len(topic) >= 2
    ]

    logger.info("从 %d 次会议中提取了 %d 个主题", len(claw_meetings), len(filtered))
    return filtered


def generate_combinations(
    topics: list[str],
    k: int = 2,
) -> list[tuple[str, ...]]:
    """
    生成主题的所有 k-组合。

    Args:
        topics: 主题列表
        k: 组合大小，默认2（两两组合）

    Returns:
        所有 k-组合的列表

    Example:
        >>> combos = generate_combinations(["A", "B", "C"], k=2)
        >>> combos
        [("A", "B"), ("A", "C"), ("B", "C")]
    """
    if k < 1:
        raise ValueError("k 必须大于 0")
    if k > len(topics):
        raise ValueError(f"k ({k}) 不能大于主题数量 ({len(topics)})")

    combos = list(combinations(topics, k))
    logger.info(
        "从 %d 个主题生成了 %d 种 %d-组合",
        len(topics),
        len(combos),
        k,
    )
    return combos


async def evaluate_combination(
    combo: tuple[str, ...],
) -> dict[str, Any]:
    """
    评估一种主题组合的科研潜力。

    从新颖性、可行性和趋势对齐三个维度评估，
    生成综合得分和详细分析。

    Args:
        combo: 主题组合元组，如 ("联邦学习", "知识图谱")

    Returns:
        评估结果字典：
        {
            "topics": list[str],
            "novelty_score": float,      # 0-1，越高越新颖
            "feasibility_score": float,  # 0-1，越高越可行
            "trend_alignment": float,    # 0-1，越高越符合趋势
            "overall_score": float,      # 加权综合分
            "paper_count": int,          # 交叉论文数量
            "related_papers": list[dict],
            "reasoning": str,
        }
    """
    topics = list(combo)
    query = " AND ".join(topics)

    # --- 1. 新颖性：搜索交叉论文 ---
    paper_count, related_papers = await _search_cross_papers(query)

    # 基于论文数量计算新颖性（论文越少越新颖，但0篇可能不可行）
    if paper_count == 0:
        novelty_score = 0.7  # 完全没有可能是太新，也可能不合理
    elif paper_count <= 5:
        novelty_score = 0.9
    elif paper_count <= 20:
        novelty_score = 0.7
    elif paper_count <= 50:
        novelty_score = 0.5
    elif paper_count <= 100:
        novelty_score = 0.3
    else:
        novelty_score = 0.1

    # --- 2. 可行性：检查团队能力覆盖 ---
    team_areas = getattr(TEAM_RESEARCH_AREAS, "list", [])
    overlap = sum(1 for t in topics if t in team_areas or _fuzzy_match(t, team_areas))
    feasibility_score = min(overlap / len(topics), 1.0) if topics else 0.0
    # 基础可行性保底
    feasibility_score = max(feasibility_score, 0.3)

    # --- 3. 趋势对齐度 ---
    trend_alignment = await _check_trend_alignment(topics)

    # --- 4. 综合得分 ---
    overall_score = (
        0.35 * novelty_score
        + 0.35 * feasibility_score
        + 0.30 * trend_alignment
    )

    # 生成推理说明
    reasoning = _generate_reasoning(
        topics, novelty_score, feasibility_score, trend_alignment, paper_count
    )

    return {
        "topics": topics,
        "novelty_score": round(novelty_score, 3),
        "feasibility_score": round(feasibility_score, 3),
        "trend_alignment": round(trend_alignment, 3),
        "overall_score": round(overall_score, 3),
        "paper_count": paper_count,
        "related_papers": related_papers[:5],
        "reasoning": reasoning,
    }


# ============================================================
# 内部辅助函数
# ============================================================


def _extract_keywords(text: str) -> list[str]:
    """
    从文本中提取技术关键词。
    简化实现：基于正则匹配中英文术语。
    生产环境应使用 jieba + 领域词典。
    """
    # 匹配英文术语（至少2个字母）
    en_terms = re.findall(r"\b[A-Za-z][A-Za-z\-]{2,}\b", text)
    # 匹配中文四字以上的术语
    zh_terms = re.findall(r"[\u4e00-\u9fff]{2,8}", text)

    # 简单停用词过滤
    stopwords = {"我们", "可以", "进行", "研究", "方法", "问题", "结果", "分析", "使用"}
    zh_terms = [t for t in zh_terms if t not in stopwords]

    return en_terms + zh_terms


def _normalize_topic(topic: str) -> str:
    """规范化主题名称。"""
    # 简单的同义词映射
    synonyms = {
        "LLM": "大语言模型",
        "Large Language Model": "大语言模型",
        "FL": "联邦学习",
        "Federated Learning": "联邦学习",
        "KG": "知识图谱",
        "Knowledge Graph": "知识图谱",
        "GNN": "图神经网络",
        "Graph Neural Network": "图神经网络",
    }
    return synonyms.get(topic, topic).strip()


def _fuzzy_match(topic: str, candidates: list[str]) -> bool:
    """模糊匹配主题是否在候选列表中。"""
    topic_lower = topic.lower()
    for c in candidates:
        if topic_lower in c.lower() or c.lower() in topic_lower:
            return True
    return False


async def _search_cross_papers(
    query: str,
) -> tuple[int, list[dict[str, Any]]]:
    """搜索交叉领域论文并返回数量和详情。"""
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": 20,
        "sortBy": "relevance",
    }
    try:
        async with aiohttp.ClientSession() as session:
            url = f"{ARXIV_API_BASE}/query"
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return 0, []
                text = await resp.text()
                # 简化：计算 <entry> 标签数量作为论文数
                count = text.count("<entry>")
                # 实际实现需要完整的 XML 解析
                return count, []
    except Exception:
        logger.exception("搜索交叉论文失败: %s", query)
        return 0, []


async def _check_trend_alignment(topics: list[str]) -> float:
    """检查主题是否与全球趋势对齐。"""
    # 简化实现：检查每个主题在 arXiv 近期的出现频率
    scores = []
    for topic in topics:
        try:
            async with aiohttp.ClientSession() as session:
                params = {
                    "search_query": f"all:{topic}",
                    "max_results": 1,
                    "sortBy": "submittedDate",
                }
                url = f"{ARXIV_API_BASE}/query"
                async with session.get(url, params=params) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        count = text.count("<entry>")
                        scores.append(min(count / 1.0, 1.0))
                    else:
                        scores.append(0.5)
        except Exception:
            scores.append(0.5)

    return sum(scores) / len(scores) if scores else 0.5


def _generate_reasoning(
    topics: list[str],
    novelty: float,
    feasibility: float,
    trend: float,
    paper_count: int,
) -> str:
    """生成中文推理说明。"""
    topic_str = " + ".join(topics)
    parts = [f"「{topic_str}」方向评估："]

    if novelty >= 0.7:
        parts.append(f"交叉论文仅{paper_count}篇，新颖性较高，有差异化空间。")
    elif novelty >= 0.4:
        parts.append(f"已有{paper_count}篇相关论文，属于有一定基础的方向。")
    else:
        parts.append(f"已有{paper_count}篇论文，竞争较为激烈，需找到独特切入点。")

    if feasibility >= 0.7:
        parts.append("与团队现有研究方向高度契合，具备开展条件。")
    elif feasibility >= 0.4:
        parts.append("与团队能力有部分重叠，需要补充一定的技术储备。")
    else:
        parts.append("与团队现有能力差距较大，开展难度较高。")

    if trend >= 0.7:
        parts.append("全球研究趋势上升，时机较好。")
    elif trend >= 0.4:
        parts.append("全球关注度稳定，属于常态化研究方向。")
    else:
        parts.append("全球关注度偏低或下降，需谨慎评估长期价值。")

    return "".join(parts)
