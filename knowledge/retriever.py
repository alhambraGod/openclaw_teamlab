"""
OpenClaw TeamLab — Knowledge Retriever
混合检索策略：语义 + 关键词 + 图谱扩展

检索管线（RAG-style）：
  1. Entity Extraction  — 从查询中识别人名、项目名等实体
  2. Semantic Search    — L2 知识节点向量检索（或关键词降级）
  3. Graph Expansion    — L3 图谱 1-hop 扩展（补充相关实体）
  4. Archival Recall    — L4 历史摘要补充（覆盖近期知识盲区）
  5. Context Assembly   — 合并去重、重要性加权、格式化 Markdown

输出为可直接注入 LLM system prompt 的 Markdown 文本。
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from knowledge.store import KnowledgeStore

logger = logging.getLogger("teamlab.knowledge.retriever")

# 最终注入 LLM 的知识块字符上限（防止超 context window）
_MAX_CONTEXT_CHARS = 4000
# 图谱扩展时最多补充的邻居节点数
_GRAPH_EXPAND_LIMIT = 5


class KnowledgeRetriever:
    """
    面向 LLM 上下文的知识检索器。

    用法：
        retriever = KnowledgeRetriever()
        knowledge_ctx = await retriever.retrieve_for_query(
            query="张三近期的研究进展如何？",
            session_entities=["张三"],
        )
        # knowledge_ctx 是 Markdown，注入 system prompt
    """

    def __init__(self, store: Optional[KnowledgeStore] = None) -> None:
        self._store = store or KnowledgeStore()

    async def retrieve_for_query(
        self,
        query: str,
        session_entities: Optional[list[str]] = None,
        k: int = 8,
        include_graph: bool = True,
        include_archival: bool = True,
    ) -> str:
        """
        执行完整检索管线，返回格式化的 Markdown 知识上下文。
        空知识库时返回空字符串。
        """
        # 1. 实体提取
        query_entities = _extract_entities(query)
        all_entities = list(dict.fromkeys((session_entities or []) + query_entities))

        collected: list[dict] = []
        seen_ids: set[int] = set()

        # 2. 语义检索
        semantic_results = await self._store.semantic_search(
            query=query,
            k=k,
            entity_ids=all_entities if all_entities else None,
        )
        for node in semantic_results:
            if node["id"] not in seen_ids:
                seen_ids.add(node["id"])
                collected.append({**node, "_source_layer": "semantic"})

        # 3. 图谱扩展（取语义 Top-3 节点的 1-hop 邻居）
        if include_graph and collected:
            top_ids = [c["id"] for c in collected[:3]]
            for nid in top_ids:
                neighbors = await self._store.get_neighbors(
                    node_id=nid,
                    depth=1,
                    max_nodes=_GRAPH_EXPAND_LIMIT,
                )
                for nb in neighbors:
                    if nb["id"] not in seen_ids:
                        seen_ids.add(nb["id"])
                        nb["_score"] = nb.get("_score", 0.3)
                        nb["_source_layer"] = "graph"
                        collected.append(nb)

        # 4. 档案补充（仅当语义结果不足时）
        if include_archival and len(collected) < k // 2 and all_entities:
            for entity in all_entities[:2]:
                summaries = await self._store.get_summaries(
                    entity_id=entity, limit=2
                )
                for s in summaries:
                    # 将摘要转换为虚拟节点格式
                    fake_node = {
                        "id": -1,
                        "entity_type": s["entity_type"],
                        "entity_id": s["entity_id"],
                        "title": f"[{s['period']} 摘要] {s['period_start']} ~ {s['period_end']}",
                        "content": s["summary_text"],
                        "importance": 60,
                        "_score": 0.4,
                        "_source_layer": "archival",
                    }
                    collected.append(fake_node)

        if not collected:
            return ""

        # 5. 记录访问（触发重要性自适应调整）
        for node in collected:
            if node.get("id", -1) > 0:
                await self._store.touch_node(node["id"])

        # 6. 格式化输出
        return _format_context(collected, max_chars=_MAX_CONTEXT_CHARS)

    async def retrieve_entity_profile(self, entity_id: str) -> str:
        """
        获取某实体的完整知识画像（所有知识节点 + 关系图 + 摘要）。
        主要用于 /api/knowledge/{entity_id}/profile 接口。
        """
        nodes = await self._store.get_entity_nodes(entity_id, limit=30)
        subgraph = await self._store.get_subgraph(entity_id)
        summaries = await self._store.get_summaries(entity_id, limit=3)

        if not nodes and not summaries:
            return f"（{entity_id} 尚无积累的知识记录）"

        lines = [f"## {entity_id} 知识画像\n"]

        if nodes:
            lines.append("### 知识节点")
            for n in nodes:
                score_badge = f" `重要性:{n['importance']}`" if n.get("importance") else ""
                lines.append(f"**{n['title']}**{score_badge}")
                lines.append(n["content"][:300] + ("..." if len(n["content"]) > 300 else ""))
                lines.append("")

        if subgraph.get("edges"):
            lines.append("### 关系图谱")
            for e in subgraph["edges"][:10]:
                lines.append(f"- `{e['from_entity']}` —[{e['relation']}]→ `{e['to_entity']}` (强度: {e['weight']:.1f})")
            lines.append("")

        if summaries:
            lines.append("### 历史摘要")
            for s in summaries:
                lines.append(f"**{s['period_start']} ~ {s['period_end']}**")
                lines.append(s["summary_text"][:500])
                lines.append("")

        full_text = "\n".join(lines)
        return full_text[:6000]  # 防止输出过长


# ── 工具函数 ────────────────────────────────────────────────────

def _extract_entities(query: str) -> list[str]:
    """
    从查询文本中提取可能的实体（人名、项目名）。
    简化实现：匹配已知 pattern，后续可替换为 NER 模型。

    目前策略：
      - 中文姓名：2-4 个汉字 + "老师/同学/博士/研究员"
      - 项目名：包含"项目"字样的 n-gram
      - 带引号的内容
    """
    entities: list[str] = []

    # 带引号的实体
    quoted = re.findall(r'[「『""](.+?)[」』""]', query)
    entities.extend(quoted)

    # 人名 pattern（中文 2-5 字后跟身份标识，或直接 2-3 字中文名）
    person_patterns = [
        r'[\u4e00-\u9fa5]{2,4}(?:老师|同学|博士|研究员|教授|学生|导师)',
        r'(?:^|[\s，。、])[\u4e00-\u9fa5]{2,3}(?=[\s，。、的]|$)',
    ]
    for p in person_patterns:
        for m in re.finditer(p, query):
            name = m.group().strip()
            # 去掉身份后缀
            name = re.sub(r'(老师|同学|博士|研究员|教授|学生|导师)$', '', name).strip()
            if len(name) >= 2:
                entities.append(name)

    # 项目名 pattern
    project_patterns = re.findall(r'[\u4e00-\u9fa5a-zA-Z0-9]+项目', query)
    entities.extend(project_patterns)

    # 去重并过滤停用词
    _STOPWORDS = {"这个", "那个", "什么", "哪个", "哪些", "怎么", "如何", "最近"}
    seen: set[str] = set()
    result: list[str] = []
    for e in entities:
        if e not in seen and e not in _STOPWORDS and len(e) >= 2:
            seen.add(e)
            result.append(e)
    return result[:5]  # 最多 5 个实体


def _format_context(
    nodes: list[dict],
    max_chars: int = _MAX_CONTEXT_CHARS,
) -> str:
    """
    将检索到的知识节点格式化为 LLM 可读的 Markdown 知识上下文。
    按 _score × importance 综合排序，超长时截断。
    """
    # 按综合分排序
    nodes = sorted(
        nodes,
        key=lambda n: n.get("_score", 0.5) * (0.5 + n.get("importance", 50) / 200),
        reverse=True,
    )

    lines = ["## 知识库上下文（Knowledge Context）\n"]
    used_chars = len(lines[0])

    # 按实体类型分组
    type_groups: dict[str, list[dict]] = {}
    for node in nodes:
        et = node.get("entity_type", "insight")
        type_groups.setdefault(et, []).append(node)

    type_labels = {
        "person": "人物知识",
        "project": "项目知识",
        "research": "研究方向",
        "concept": "概念与技术",
        "insight": "AI 洞见",
        "event": "事件记录",
    }

    for entity_type, group in type_groups.items():
        label = type_labels.get(entity_type, entity_type)
        section_header = f"\n### {label}\n"
        if used_chars + len(section_header) > max_chars:
            break
        lines.append(section_header)
        used_chars += len(section_header)

        for node in group:
            title = node.get("title", "")
            content = node.get("content", "")
            entity_id = node.get("entity_id", "")
            source_label = {
                "archival": "📚",
                "graph": "🔗",
                "semantic": "🧠",
            }.get(node.get("_source_layer", ""), "")

            snippet = content[:400] + ("..." if len(content) > 400 else "")
            entry = f"**{entity_id} · {title}** {source_label}\n{snippet}\n\n"

            if used_chars + len(entry) > max_chars:
                # 尝试截断 snippet
                remaining = max_chars - used_chars - len(f"**{entity_id} · {title}** {source_label}\n\n\n")
                if remaining > 80:
                    entry = f"**{entity_id} · {title}** {source_label}\n{content[:remaining]}...\n\n"
                    lines.append(entry)
                break

            lines.append(entry)
            used_chars += len(entry)

    return "".join(lines)
