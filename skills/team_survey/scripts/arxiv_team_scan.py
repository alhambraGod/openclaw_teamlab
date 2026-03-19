"""
全球团队调研 - arXiv 与学术数据库扫描模块

搜索与本团队研究方向相似的全球科研团队，
采集发表指标并进行对标分析。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import aiohttp

from config.settings import (
    ARXIV_API_BASE,
    SEMANTIC_SCHOLAR_API_BASE,
    SEMANTIC_SCHOLAR_API_KEY,
)

logger = logging.getLogger(__name__)

ARXIV_SEARCH_URL = f"{ARXIV_API_BASE}/query"
S2_SEARCH_URL = f"{SEMANTIC_SCHOLAR_API_BASE}/paper/search"
S2_AUTHOR_URL = f"{SEMANTIC_SCHOLAR_API_BASE}/author"


@dataclass
class TeamInfo:
    """科研团队信息"""
    team_name: str
    institution: str
    leader: str
    homepage: Optional[str] = None
    research_focus: list[str] = field(default_factory=list)
    recent_papers_count: int = 0
    avg_citations: float = 0.0
    top_papers: list[dict[str, Any]] = field(default_factory=list)
    relevance_score: float = 0.0


async def search_similar_teams(
    research_areas: list[str],
    max_results: int = 20,
) -> list[dict[str, Any]]:
    """
    搜索与指定研究方向相似的科研团队。

    通过 arXiv 和 Semantic Scholar 的论文搜索，
    对作者进行聚类分析，识别活跃的科研团队。

    Args:
        research_areas: 研究方向关键词列表（英文）
        max_results: 最大返回团队数

    Returns:
        团队信息列表，按相关性得分降序排列：
        [
            {
                "team_name": str,
                "institution": str,
                "leader": str,
                "research_focus": [str],
                "recent_papers_count": int,
                "avg_citations": float,
                "top_papers": [{"title": str, "venue": str, "citations": int}],
                "relevance_score": float,
            }
        ]
    """
    query = " AND ".join(research_areas)
    teams: dict[str, TeamInfo] = {}

    # --- 搜索 arXiv ---
    try:
        arxiv_papers = await _search_arxiv(query, max_papers=200)
        _cluster_authors_from_papers(arxiv_papers, teams)
    except Exception:
        logger.exception("arXiv 搜索失败")

    # --- 搜索 Semantic Scholar ---
    try:
        s2_papers = await _search_semantic_scholar(query, max_papers=200)
        _cluster_authors_from_papers(s2_papers, teams)
    except Exception:
        logger.exception("Semantic Scholar 搜索失败")

    # 按相关性排序
    team_list = sorted(
        teams.values(),
        key=lambda t: t.relevance_score,
        reverse=True,
    )[:max_results]

    results = []
    for team in team_list:
        results.append(
            {
                "team_name": team.team_name,
                "institution": team.institution,
                "leader": team.leader,
                "homepage": team.homepage,
                "research_focus": team.research_focus,
                "recent_papers_count": team.recent_papers_count,
                "avg_citations": team.avg_citations,
                "top_papers": team.top_papers[:5],
                "relevance_score": round(team.relevance_score, 4),
            }
        )

    logger.info("搜索到 %d 个相似团队", len(results))
    return results


async def compare_metrics(
    our_team: dict[str, Any],
    other_teams: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    将本团队与其他团队进行多维度指标对比。

    Args:
        our_team: 本团队信息字典，包含：
            - name: 团队名称
            - papers: 论文列表
            - research_focus: 研究方向
            - members_count: 成员数
        other_teams: 其他团队信息列表（search_similar_teams 的输出）

    Returns:
        对比分析结果：
        {
            "metrics_table": [
                {"team": str, "papers": int, "avg_citations": float, "h_index": int}
            ],
            "our_strengths": [str],
            "our_gaps": [str],
            "opportunities": [str],
            "direction_overlap": {
                "high_competition": [str],  # 多团队竞争的方向
                "blue_ocean": [str],        # 少有团队关注的方向
            },
        }
    """
    our_papers = our_team.get("papers", [])
    our_paper_count = len(our_papers)
    our_citations = [p.get("citations", 0) for p in our_papers]
    our_avg_citations = (
        sum(our_citations) / len(our_citations) if our_citations else 0.0
    )
    our_focus = set(our_team.get("research_focus", []))

    metrics_table = [
        {
            "team": our_team.get("name", "Our Team"),
            "papers": our_paper_count,
            "avg_citations": round(our_avg_citations, 1),
            "members": our_team.get("members_count", 0),
        }
    ]

    our_strengths = []
    our_gaps = []
    opportunities = []
    all_other_focus: list[str] = []

    for team in other_teams:
        metrics_table.append(
            {
                "team": team["team_name"],
                "papers": team["recent_papers_count"],
                "avg_citations": team["avg_citations"],
                "members": team.get("members_count", 0),
            }
        )
        all_other_focus.extend(team.get("research_focus", []))

        # 发表量对比
        if our_paper_count > team["recent_papers_count"] * 1.2:
            our_strengths.append(
                f"发表数量领先于 {team['team_name']} "
                f"({our_paper_count} vs {team['recent_papers_count']})"
            )
        elif team["recent_papers_count"] > our_paper_count * 1.2:
            our_gaps.append(
                f"发表数量落后于 {team['team_name']} "
                f"({our_paper_count} vs {team['recent_papers_count']})"
            )

        # 引用量对比
        if our_avg_citations > team["avg_citations"] * 1.2:
            our_strengths.append(
                f"平均引用量高于 {team['team_name']}"
            )
        elif team["avg_citations"] > our_avg_citations * 1.2:
            our_gaps.append(
                f"平均引用量低于 {team['team_name']}"
            )

    # 方向竞争分析
    from collections import Counter
    focus_counter = Counter(all_other_focus)

    high_competition = [
        topic for topic, count in focus_counter.items()
        if count >= len(other_teams) * 0.5 and topic in our_focus
    ]
    blue_ocean = [
        topic for topic in our_focus
        if focus_counter.get(topic, 0) <= 1
    ]

    if blue_ocean:
        for topic in blue_ocean:
            opportunities.append(f"在 {topic} 方向竞争较少，有先发优势")

    return {
        "metrics_table": metrics_table,
        "our_strengths": our_strengths,
        "our_gaps": our_gaps,
        "opportunities": opportunities,
        "direction_overlap": {
            "high_competition": high_competition,
            "blue_ocean": blue_ocean,
        },
    }


# ============================================================
# 内部辅助函数
# ============================================================


async def _search_arxiv(query: str, max_papers: int = 200) -> list[dict]:
    """从 arXiv API 搜索论文。"""
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": max_papers,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(ARXIV_SEARCH_URL, params=params) as resp:
            if resp.status != 200:
                logger.warning("arXiv 返回 %d", resp.status)
                return []
            text = await resp.text()
            # 简化处理：实际实现需要解析 Atom XML
            return _parse_arxiv_response(text)


async def _search_semantic_scholar(
    query: str, max_papers: int = 200
) -> list[dict]:
    """从 Semantic Scholar API 搜索论文。"""
    headers = {}
    if SEMANTIC_SCHOLAR_API_KEY:
        headers["x-api-key"] = SEMANTIC_SCHOLAR_API_KEY

    params = {
        "query": query,
        "limit": min(max_papers, 100),
        "fields": "title,authors,venue,year,citationCount,externalIds",
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(
            S2_SEARCH_URL, params=params, headers=headers
        ) as resp:
            if resp.status != 200:
                logger.warning("Semantic Scholar 返回 %d", resp.status)
                return []
            data = await resp.json()
            papers = data.get("data", [])
            return [
                {
                    "title": p.get("title", ""),
                    "authors": [
                        a.get("name", "") for a in p.get("authors", [])
                    ],
                    "venue": p.get("venue", ""),
                    "year": p.get("year"),
                    "citations": p.get("citationCount", 0),
                    "source": "semantic_scholar",
                }
                for p in papers
            ]


def _parse_arxiv_response(xml_text: str) -> list[dict]:
    """解析 arXiv Atom XML 响应为论文列表。"""
    import xml.etree.ElementTree as ET

    papers = []
    try:
        root = ET.fromstring(xml_text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("atom:entry", ns):
            title_el = entry.find("atom:title", ns)
            authors = [
                a.find("atom:name", ns).text
                for a in entry.findall("atom:author", ns)
                if a.find("atom:name", ns) is not None
            ]
            papers.append(
                {
                    "title": title_el.text.strip() if title_el is not None else "",
                    "authors": authors,
                    "venue": "arXiv",
                    "year": None,
                    "citations": 0,
                    "source": "arxiv",
                }
            )
    except ET.ParseError:
        logger.exception("解析 arXiv XML 失败")

    return papers


def _cluster_authors_from_papers(
    papers: list[dict], teams: dict[str, TeamInfo]
) -> None:
    """
    根据论文作者共现关系聚类，识别团队。
    简化实现：以第一作者的最后一位合作者（通常是通讯作者/导师）为团队标识。
    """
    from collections import defaultdict

    leader_papers: dict[str, list[dict]] = defaultdict(list)

    for paper in papers:
        authors = paper.get("authors", [])
        if not authors:
            continue
        # 启发式：最后一位作者通常是通讯作者/团队负责人
        leader = authors[-1] if len(authors) > 1 else authors[0]
        leader_papers[leader].append(paper)

    for leader, lp in leader_papers.items():
        if len(lp) < 3:
            # 过滤发表量过少的
            continue
        citations = [p.get("citations", 0) for p in lp]
        avg_cit = sum(citations) / len(citations) if citations else 0.0

        # 按引用量排序取 top papers
        top = sorted(lp, key=lambda x: x.get("citations", 0), reverse=True)[:5]

        if leader not in teams:
            teams[leader] = TeamInfo(
                team_name=f"{leader} Group",
                institution="",
                leader=leader,
                recent_papers_count=len(lp),
                avg_citations=round(avg_cit, 1),
                top_papers=[
                    {
                        "title": p["title"],
                        "venue": p.get("venue", ""),
                        "citations": p.get("citations", 0),
                    }
                    for p in top
                ],
                relevance_score=len(lp) * 0.3 + avg_cit * 0.01,
            )
        else:
            existing = teams[leader]
            existing.recent_papers_count += len(lp)
            existing.relevance_score += len(lp) * 0.3
