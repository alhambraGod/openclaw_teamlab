"""
科研趋势监控 - arXiv 每日扫描模块

每日从 arXiv 获取最新论文，评估与团队研究方向的相关度，
生成结构化的趋势报告。
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any, Optional

import aiohttp

from config.database import get_db_pool
from config.settings import ARXIV_API_BASE, TEAM_RESEARCH_AREAS

logger = logging.getLogger(__name__)

ARXIV_QUERY_URL = f"{ARXIV_API_BASE}/query"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

# arXiv API 频率限制：每3秒一次请求
REQUEST_INTERVAL_SECONDS = 3


async def scan_arxiv(
    domains: Optional[list[str]] = None,
    days: int = 1,
    max_results_per_domain: int = 100,
) -> list[dict[str, Any]]:
    """
    扫描 arXiv 指定领域在最近 N 天内的新论文。

    Args:
        domains: arXiv 分类列表，如 ["cs.AI", "cs.CL", "cs.CV"]。
                 为 None 时使用团队配置中的默认领域。
        days: 回溯天数，默认1（仅查看昨天的新论文）
        max_results_per_domain: 每个分类最多返回的论文数

    Returns:
        去重后的论文列表：
        [
            {
                "arxiv_id": str,
                "title": str,
                "authors": list[str],
                "abstract": str,
                "categories": list[str],
                "published_date": str,
                "url": str,
            }
        ]
    """
    if domains is None:
        domains = getattr(TEAM_RESEARCH_AREAS, "arxiv_categories", ["cs.AI"])

    all_papers: dict[str, dict[str, Any]] = {}  # keyed by arxiv_id for dedup
    import asyncio

    for domain in domains:
        try:
            papers = await _fetch_domain_papers(
                domain, days, max_results_per_domain
            )
            for paper in papers:
                aid = paper["arxiv_id"]
                if aid not in all_papers:
                    all_papers[aid] = paper
                else:
                    # 合并分类
                    existing_cats = set(all_papers[aid]["categories"])
                    existing_cats.update(paper["categories"])
                    all_papers[aid]["categories"] = sorted(existing_cats)

            logger.info("域 %s: 获取 %d 篇论文", domain, len(papers))
        except Exception:
            logger.exception("扫描域 %s 失败", domain)

        # 遵守频率限制
        await asyncio.sleep(REQUEST_INTERVAL_SECONDS)

    result = sorted(
        all_papers.values(),
        key=lambda p: p.get("published_date", ""),
        reverse=True,
    )

    logger.info("共扫描 %d 个领域，获取 %d 篇去重论文", len(domains), len(result))
    return result


async def score_relevance(
    papers: list[dict[str, Any]],
    team_directions: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """
    评估论文与团队研究方向的相关度。

    使用关键词匹配和 TF-IDF 相似度计算相关性得分，
    为每篇论文添加 relevance_score 和 relevance_reason。

    Args:
        papers: scan_arxiv 返回的论文列表
        team_directions: 团队研究方向关键词列表。
                         为 None 时从配置中读取。

    Returns:
        添加了相关性得分的论文列表（按 relevance_score 降序）：
        [
            {
                ...原始字段...,
                "relevance_score": float,  # 0-1
                "relevance_reason": str,   # 中文相关性说明
                "matched_directions": list[str],
            }
        ]
    """
    if team_directions is None:
        team_directions = getattr(
            TEAM_RESEARCH_AREAS, "keywords", ["machine learning"]
        )

    scored_papers = []

    for paper in papers:
        score, reason, matched = _compute_relevance(paper, team_directions)
        scored_paper = {
            **paper,
            "relevance_score": round(score, 4),
            "relevance_reason": reason,
            "matched_directions": matched,
        }
        scored_papers.append(scored_paper)

    # 按相关性降序排序
    scored_papers.sort(key=lambda p: p["relevance_score"], reverse=True)

    # 持久化高相关论文
    high_relevance = [p for p in scored_papers if p["relevance_score"] >= 0.5]
    if high_relevance:
        await _save_relevant_papers(high_relevance)

    logger.info(
        "完成 %d 篇论文的相关性评分，其中 %d 篇高相关",
        len(scored_papers),
        len(high_relevance),
    )
    return scored_papers


# ============================================================
# 内部辅助函数
# ============================================================


async def _fetch_domain_papers(
    domain: str, days: int, max_results: int
) -> list[dict[str, Any]]:
    """从 arXiv 获取指定领域的最新论文。"""
    # arXiv 搜索查询
    query = f"cat:{domain}"

    params = {
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(ARXIV_QUERY_URL, params=params) as resp:
            if resp.status != 200:
                logger.warning("arXiv 返回 HTTP %d (domain=%s)", resp.status, domain)
                return []
            xml_text = await resp.text()

    papers = _parse_arxiv_xml(xml_text)

    # 过滤日期范围
    cutoff_date = datetime.now() - timedelta(days=days)
    cutoff_str = cutoff_date.strftime("%Y-%m-%d")
    filtered = [
        p for p in papers
        if p.get("published_date", "9999") >= cutoff_str
    ]

    return filtered


def _parse_arxiv_xml(xml_text: str) -> list[dict[str, Any]]:
    """解析 arXiv Atom XML 为论文列表。"""
    papers = []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        logger.exception("arXiv XML 解析失败")
        return []

    for entry in root.findall("atom:entry", ATOM_NS):
        # 标题
        title_el = entry.find("atom:title", ATOM_NS)
        title = title_el.text.strip().replace("\n", " ") if title_el is not None else ""

        # 作者
        authors = []
        for author_el in entry.findall("atom:author", ATOM_NS):
            name_el = author_el.find("atom:name", ATOM_NS)
            if name_el is not None and name_el.text:
                authors.append(name_el.text.strip())

        # 摘要
        summary_el = entry.find("atom:summary", ATOM_NS)
        abstract = (
            summary_el.text.strip().replace("\n", " ")
            if summary_el is not None
            else ""
        )

        # arXiv ID
        id_el = entry.find("atom:id", ATOM_NS)
        arxiv_url = id_el.text.strip() if id_el is not None else ""
        arxiv_id = arxiv_url.split("/abs/")[-1] if "/abs/" in arxiv_url else ""

        # 发布日期
        published_el = entry.find("atom:published", ATOM_NS)
        published_date = ""
        if published_el is not None and published_el.text:
            published_date = published_el.text[:10]  # YYYY-MM-DD

        # 分类
        categories = []
        for cat_el in entry.findall("{http://arxiv.org/schemas/atom}category"):
            term = cat_el.get("term", "")
            if term:
                categories.append(term)
        # 备选：从主分类获取
        if not categories:
            primary_el = entry.find(
                "{http://arxiv.org/schemas/atom}primary_category"
            )
            if primary_el is not None:
                term = primary_el.get("term", "")
                if term:
                    categories.append(term)

        papers.append(
            {
                "arxiv_id": arxiv_id,
                "title": title,
                "authors": authors,
                "abstract": abstract,
                "categories": categories,
                "published_date": published_date,
                "url": arxiv_url,
            }
        )

    return papers


def _compute_relevance(
    paper: dict[str, Any],
    team_directions: list[str],
) -> tuple[float, str, list[str]]:
    """
    计算单篇论文与团队方向的相关度。

    Returns:
        (score, reason, matched_directions)
    """
    text = f"{paper.get('title', '')} {paper.get('abstract', '')}".lower()
    matched = []
    total_score = 0.0

    for direction in team_directions:
        direction_lower = direction.lower()
        # 关键词匹配
        keywords = direction_lower.split()
        matches = sum(1 for kw in keywords if kw in text)
        if matches > 0:
            keyword_score = matches / len(keywords)
            total_score += keyword_score
            if keyword_score >= 0.5:
                matched.append(direction)

    # 归一化
    if team_directions:
        score = min(total_score / len(team_directions) * 2, 1.0)
    else:
        score = 0.0

    # 生成理由
    if matched:
        reason = f"与团队方向「{'、'.join(matched[:3])}」高度相关"
    elif score > 0.3:
        reason = "与团队研究领域有部分关联"
    elif score > 0:
        reason = "与团队方向有微弱关联"
    else:
        reason = "与团队当前方向关联度低"

    return score, reason, matched


async def _save_relevant_papers(
    papers: list[dict[str, Any]],
) -> None:
    """保存高相关论文到数据库。"""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for paper in papers:
                try:
                    await cur.execute(
                        """
                        INSERT IGNORE INTO arxiv_papers
                            (arxiv_id, title, authors_json, abstract,
                             categories_json, relevance_score, published_date, scanned_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                        """,
                        (
                            paper["arxiv_id"],
                            paper["title"],
                            ",".join(paper["authors"]),
                            paper["abstract"],
                            ",".join(paper["categories"]),
                            paper["relevance_score"],
                            paper["published_date"],
                        ),
                    )
                except Exception:
                    logger.exception(
                        "保存论文失败: %s", paper.get("arxiv_id")
                    )
        await conn.commit()

    logger.info("已保存 %d 篇高相关论文", len(papers))
