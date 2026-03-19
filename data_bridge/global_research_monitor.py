"""
Global Research Monitor
=======================
每日从 arxiv / Semantic Scholar 抓取全球最新论文，与团队研究方向智能匹配，
生成"全球热点与团队机遇"洞见，存储到 claw_pi_agent_insights 供 Agent 随时检索。

设计原则：
- 免费 API（arxiv XML feed、Semantic Scholar public API），无需 key
- 异步 IO，不阻塞调度器
- 结果向量化（关键词/TF-IDF 轻量级匹配）后由 LLM 生成最终洞见
- 幂等：同一天重复运行只更新，不重复插入
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from typing import Optional

import httpx

logger = logging.getLogger("teamlab.global_research_monitor")

# ── arxiv 主题映射 ─────────────────────────────────────────────────────────
# 根据团队研究关键词，映射到 arxiv 搜索 query（cs.AI / cs.CL / cs.LG 等）
TEAM_TOPIC_QUERIES = [
    {
        "topic": "LLM Mechanistic Interpretability",
        "arxiv_query": "abs:mechanistic+interpretability+LLM",
        "zh": "大模型机械可解释性",
    },
    {
        "topic": "LLM Alignment & Steering",
        "arxiv_query": "abs:LLM+alignment+steering+activation",
        "zh": "大模型对齐与激活转向",
    },
    {
        "topic": "AI Agent Simulation",
        "arxiv_query": "abs:LLM+agent+social+simulation",
        "zh": "大模型智能体社会模拟",
    },
    {
        "topic": "AI Education & Learning Science",
        "arxiv_query": "abs:LLM+education+learning+science",
        "zh": "AI教育与学习科学",
    },
    {
        "topic": "Research Automation & AutoML",
        "arxiv_query": "abs:automated+research+AI+scientist",
        "zh": "科研自动化与AI科学家",
    },
    {
        "topic": "Sycophancy in LLMs",
        "arxiv_query": "abs:sycophancy+large+language+model",
        "zh": "大模型谄媚行为",
    },
]

ARXIV_BASE = "https://export.arxiv.org/api/query"
S2_BASE = "https://api.semanticscholar.org/graph/v1"
ARXIV_NS = "{http://www.w3.org/2005/Atom}"


# ── arxiv 抓取 ────────────────────────────────────────────────────────────

async def _fetch_arxiv(query: str, max_results: int = 8) -> list[dict]:
    """Fetch recent papers from arxiv for a given query string."""
    params = {
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": str(max_results),
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(ARXIV_BASE, params=params)
            resp.raise_for_status()
    except Exception as exc:
        logger.warning("arxiv fetch failed for %s: %s", query, exc)
        return []

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as exc:
        logger.warning("arxiv XML parse error: %s", exc)
        return []

    papers = []
    for entry in root.findall(f"{ARXIV_NS}entry"):
        title_el = entry.find(f"{ARXIV_NS}title")
        summary_el = entry.find(f"{ARXIV_NS}summary")
        published_el = entry.find(f"{ARXIV_NS}published")
        link_el = entry.find(f"{ARXIV_NS}id")
        authors = [
            a.find(f"{ARXIV_NS}name").text
            for a in entry.findall(f"{ARXIV_NS}author")
            if a.find(f"{ARXIV_NS}name") is not None
        ]
        papers.append(
            {
                "title": (title_el.text or "").strip(),
                "abstract": (summary_el.text or "").strip()[:600],
                "published": (published_el.text or "")[:10],
                "url": (link_el.text or "").strip(),
                "authors": authors[:5],
                "source": "arxiv",
            }
        )
    return papers


async def _fetch_semantic_scholar(query: str, limit: int = 5) -> list[dict]:
    """Fetch papers from Semantic Scholar public API."""
    params = {
        "query": query,
        "fields": "title,abstract,year,authors,url,externalIds",
        "limit": str(limit),
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(f"{S2_BASE}/paper/search", params=params)
            if resp.status_code == 429:
                logger.warning("Semantic Scholar rate limited, skipping")
                return []
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Semantic Scholar fetch failed for %s: %s", query, exc)
        return []

    papers = []
    for item in data.get("data", []):
        arxiv_id = (item.get("externalIds") or {}).get("ArXiv", "")
        papers.append(
            {
                "title": item.get("title", ""),
                "abstract": (item.get("abstract") or "")[:600],
                "published": str(item.get("year", "")),
                "url": item.get("url") or (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""),
                "authors": [
                    a.get("name", "") for a in (item.get("authors") or [])[:5]
                ],
                "source": "semantic_scholar",
            }
        )
    return papers


# ── 团队研究关键词提取 ────────────────────────────────────────────────────

async def _get_team_research_directions() -> list[dict]:
    """从数据库提取团队各项目的研究计划关键词。"""
    try:
        from sqlalchemy import text
        from config.coevo_db import get_coevo_db

        async with get_coevo_db() as db:
            r = await db.execute(text("""
                SELECT rp.content, p.project_name, u.username as owner
                FROM research_plans rp
                JOIN users u ON u.id = rp.user_id AND u.is_active = 1
                LEFT JOIN projects p ON p.id = (
                    SELECT pm.project_id FROM project_members pm
                    WHERE pm.user_id = rp.user_id AND pm.project_id IS NOT NULL
                    LIMIT 1
                )
                WHERE rp.content IS NOT NULL
                ORDER BY rp.created_at DESC
                LIMIT 20
            """))
            plans = r.mappings().all()

        directions = []
        for plan in plans:
            content = plan.get("content") or ""
            if isinstance(content, dict):
                text_content = json.dumps(content, ensure_ascii=False)
            else:
                text_content = str(content)
            directions.append({
                "owner": plan.get("owner", "unknown"),
                "project": plan.get("project_name") or "未知项目",
                "content": text_content[:400],
            })
        return directions
    except Exception as exc:
        logger.warning("Failed to load team research directions: %s", exc)
        return []


# ── LLM 洞见生成 ─────────────────────────────────────────────────────────

async def _generate_insight_for_topic(
    topic_info: dict,
    papers: list[dict],
    team_directions: list[dict],
) -> str:
    """用 LLM 生成该主题的"全球热点与团队机遇"摘要。"""
    if not papers:
        return ""

    from config.settings import settings
    import openai

    papers_text = "\n".join(
        f"- [{p['published']}] **{p['title']}** ({', '.join(p['authors'][:2])})\n  {p['abstract'][:300]}"
        for p in papers[:6]
    )

    team_text = "\n".join(
        f"- {d['owner']} ({d['project']}): {d['content'][:200]}"
        for d in team_directions[:8]
    )

    prompt = f"""你是一个顶尖的科研助手，负责帮助 PI 了解全球最新研究进展，并发现团队的合作机遇。

## 当前研究主题
{topic_info['zh']}（{topic_info['topic']}）

## 最新全球论文（近期 arxiv/Semantic Scholar）
{papers_text}

## 团队当前研究方向
{team_text if team_text else "（暂无详细研究计划数据）"}

请完成以下分析（中文，简洁有力）：

### 1. 全球热点摘要（2-3句）
当前该方向全球最热门的研究焦点是什么？

### 2. 团队相关度
团队哪些成员/项目与上述热点最接近？有哪些潜在切入点？

### 3. 行动建议（1-2条）
针对 PI，最值得关注的 1-2 篇论文是哪些？建议如何跟进？

请保持简洁，全文不超过 300 字。"""

    try:
        client = openai.AsyncOpenAI(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY or "unused",
        )
        resp = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=600,
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        logger.warning("LLM insight generation failed: %s", exc)
        # 降级：直接返回论文列表摘要
        return f"**{topic_info['zh']} — 最新动态**\n\n" + papers_text


# ── 跨项目协作机会分析 ───────────────────────────────────────────────────

async def analyze_cross_project_collaboration() -> str:
    """
    分析跨项目协作机会：
    基于全部项目的研究方向、成员专长，识别不同项目间的互补点和合作价值。
    """
    try:
        from sqlalchemy import text
        from config.coevo_db import get_coevo_db
        from config.settings import settings
        import openai

        # 取每个项目的成员和研究目标
        async with get_coevo_db() as db:
            r = await db.execute(text("""
                SELECT p.project_name, p.description,
                       GROUP_CONCAT(u.username ORDER BY u.username SEPARATOR '、') as members,
                       GROUP_CONCAT(DISTINCT pm.quarterly_goal ORDER BY u.username SEPARATOR '；') as goals
                FROM projects p
                JOIN project_members pm ON pm.project_id = p.id
                JOIN users u ON u.id = pm.user_id AND u.is_active = 1
                WHERE p.is_active = 1
                GROUP BY p.id, p.project_name, p.description
                LIMIT 10
            """))
            projects = r.mappings().all()

        if len(projects) < 2:
            return "（团队项目数量不足，暂无跨项目协作分析）"

        projects_text = "\n\n".join(
            f"**{p['project_name']}**\n成员: {p['members']}\n目标: {(p['goals'] or '')[:300]}"
            for p in projects
        )

        prompt = f"""你是科研协作专家。请分析以下多个项目之间的潜在协作机会。

## 各项目情况
{projects_text}

请识别：
1. **技术互补**：哪两个项目在技术方法上可以互相赋能？（列出2-3对）
2. **数据/资源共享**：哪些项目的数据集、标注资源可以共用？
3. **联合发表机会**：哪些项目有潜力合作产出更高影响力的论文？
4. **风险互锁**：哪些项目若同时遇到瓶颈会相互阻塞？

请用简洁的中文列点，总字数不超过 400 字。"""

        client = openai.AsyncOpenAI(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY or "unused",
        )
        resp = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=800,
        )
        return resp.choices[0].message.content.strip()

    except Exception as exc:
        logger.error("Cross-project analysis failed: %s", exc, exc_info=True)
        return f"[ERROR] {exc}"


# ── 存储洞见 ─────────────────────────────────────────────────────────────

async def _save_insight(insight_type: str, subject: str, content: str, metadata: dict | None = None):
    """将洞见持久化到 claw_pi_agent_insights 表。"""
    try:
        from sqlalchemy import text
        from config.database import get_db
        today = date.today().isoformat()

        async with get_db() as db:
            # 同一天同类型洞见 → 更新，不重复插入
            existing = await db.execute(text("""
                SELECT id FROM claw_pi_agent_insights
                WHERE insight_type = :t AND subject = :s AND DATE(created_at) = :d
                LIMIT 1
            """), {"t": insight_type, "s": subject, "d": today})
            row = existing.fetchone()

            if row:
                await db.execute(text("""
                    UPDATE claw_pi_agent_insights
                    SET content = :c, metadata = :m, created_at = NOW()
                    WHERE id = :id
                """), {
                    "c": content,
                    "m": json.dumps(metadata or {}, ensure_ascii=False),
                    "id": row[0],
                })
            else:
                await db.execute(text("""
                    INSERT INTO claw_pi_agent_insights (insight_type, subject, content, metadata, created_at)
                    VALUES (:t, :s, :c, :m, NOW())
                """), {
                    "t": insight_type,
                    "s": subject,
                    "c": content,
                    "m": json.dumps(metadata or {}, ensure_ascii=False),
                })
            await db.commit()
    except Exception as exc:
        logger.error("Failed to save insight: %s", exc)


# ── 主入口 ────────────────────────────────────────────────────────────────

async def scan_global_research(
    topics: list[dict] | None = None,
    max_papers_per_topic: int = 6,
    with_cross_project: bool = True,
) -> dict:
    """
    全球研究扫描主入口。
    - 抓取各研究主题的最新论文
    - 与团队方向匹配，生成 LLM 洞见
    - 分析跨项目协作机会
    - 全部存入 claw_pi_agent_insights

    Returns: {"scanned_topics": N, "papers_found": N, "insights_saved": N}
    """
    topics = topics or TEAM_TOPIC_QUERIES
    team_directions = await _get_team_research_directions()
    logger.info("Global research scan started: %d topics, %d team directions", len(topics), len(team_directions))

    scanned = 0
    total_papers = 0
    insights_saved = 0

    for topic_info in topics:
        try:
            # 并发从 arxiv 和 S2 拉取
            arxiv_papers, s2_papers = await asyncio.gather(
                _fetch_arxiv(topic_info["arxiv_query"], max_results=max_papers_per_topic),
                _fetch_semantic_scholar(topic_info["topic"], limit=max_papers_per_topic // 2),
            )
            # 合并去重（按 title 前60字符）
            seen_titles: set[str] = set()
            papers: list[dict] = []
            for p in arxiv_papers + s2_papers:
                key = p["title"][:60].lower()
                if key not in seen_titles:
                    seen_titles.add(key)
                    papers.append(p)

            total_papers += len(papers)
            logger.info("Topic '%s': %d papers fetched", topic_info["zh"], len(papers))

            if not papers:
                scanned += 1
                continue

            # LLM 生成洞见
            insight_text = await _generate_insight_for_topic(topic_info, papers, team_directions)
            if insight_text:
                await _save_insight(
                    insight_type="global_research",
                    subject=topic_info["zh"],
                    content=insight_text,
                    metadata={
                        "topic": topic_info["topic"],
                        "papers_count": len(papers),
                        "paper_titles": [p["title"] for p in papers[:5]],
                        "scan_date": date.today().isoformat(),
                    },
                )
                insights_saved += 1

            scanned += 1
            # 避免频繁请求被限流
            await asyncio.sleep(1.5)

        except Exception as exc:
            logger.error("Topic scan failed for '%s': %s", topic_info.get("zh"), exc, exc_info=True)
            scanned += 1

    # 跨项目协作分析
    if with_cross_project:
        try:
            cross_insight = await analyze_cross_project_collaboration()
            if cross_insight:
                await _save_insight(
                    insight_type="cross_project",
                    subject="跨项目协作机会",
                    content=cross_insight,
                    metadata={"scan_date": date.today().isoformat()},
                )
                insights_saved += 1
        except Exception as exc:
            logger.error("Cross-project analysis failed: %s", exc)

    result = {
        "scanned_topics": scanned,
        "papers_found": total_papers,
        "insights_saved": insights_saved,
        "scan_date": date.today().isoformat(),
    }
    logger.info("Global research scan complete: %s", result)
    return result


async def get_latest_insights(
    insight_type: str | None = None,
    topic: str | None = None,
    days: int = 7,
    limit: int = 10,
) -> list[dict]:
    """
    检索近期全球研究洞见。
    - insight_type: 'global_research' | 'cross_project' | None（全部）
    - topic: 按主题关键词过滤
    - days: 最近N天
    """
    try:
        from sqlalchemy import text
        from config.database import get_db

        where_clauses = ["created_at >= DATE_SUB(NOW(), INTERVAL :days DAY)"]
        params: dict = {"days": days, "limit": limit}

        if insight_type:
            where_clauses.append("insight_type = :itype")
            params["itype"] = insight_type
        if topic:
            where_clauses.append("(subject LIKE :topic OR content LIKE :topic)")
            params["topic"] = f"%{topic}%"

        where_sql = " AND ".join(where_clauses)
        async with get_db() as db:
            r = await db.execute(text(f"""
                SELECT id, insight_type, subject, content, metadata, created_at
                FROM claw_pi_agent_insights
                WHERE {where_sql}
                ORDER BY created_at DESC
                LIMIT :limit
            """), params)
            rows = r.mappings().all()

        results = []
        for row in rows:
            meta = row.get("metadata")
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, ValueError):
                    meta = {}
            results.append({
                "id": row["id"],
                "type": row["insight_type"],
                "subject": row["subject"],
                "content": row["content"],
                "metadata": meta or {},
                "created_at": str(row["created_at"]),
            })
        return results
    except Exception as exc:
        logger.error("get_latest_insights failed: %s", exc)
        return []
