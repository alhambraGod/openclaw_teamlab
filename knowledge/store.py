"""
OpenClaw TeamLab — Knowledge Store
知识图谱 CRUD + 语义检索核心层

设计原则：
  - 单一职责：本模块只负责持久化读写，不含 LLM 调用
  - 幂等性：upsert_node 相同 (entity_type, entity_id, title) 时更新而非重复插入
  - 自适应重要性：节点被访问时自动提升重要性（使用衰减公式）
  - 向量优雅降级：无嵌入时退化为关键词检索，功能完整但精度降低
  - 图谱懒加载：仅查询时才加载邻居，不主动维护内存图
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import get_db
from knowledge.embedder import EmbeddingService, get_embedder

logger = logging.getLogger("teamlab.knowledge.store")

# 重要性提升步长（每次访问 +δ，上限 100）
_IMPORTANCE_ACCESS_DELTA = 2
# 批量检索时最多从 DB 拉取的候选节点数（Python 端再做向量重排）
_CANDIDATE_LIMIT = 200


class KnowledgeStore:
    """
    L2/L3 知识存储层。

    用法（FastAPI / worker 中均可）：
        ks = KnowledgeStore()
        node_id = await ks.upsert_node(
            entity_type="person",
            entity_id="张三",
            title="张三的 NLP 研究进展",
            content="张三目前研究 RAG 系统优化，近期...",
            metadata={"project": "项目A"},
        )
        await ks.add_edge(node_id, other_id, relation="works_on")
        results = await ks.semantic_search("张三研究方向", k=5)
    """

    def __init__(self, embedder: Optional[EmbeddingService] = None) -> None:
        self._emb = embedder or get_embedder()

    # ══════════════════════════════════════════════════════════════
    #  节点 CRUD
    # ══════════════════════════════════════════════════════════════

    async def upsert_node(
        self,
        entity_type: str,
        entity_id: str,
        title: str,
        content: str,
        source: str = "librarian",
        importance: int = 50,
        confidence: float = 0.80,
        metadata: Optional[dict] = None,
        expires_at: Optional[datetime] = None,
        auto_embed: bool = True,
    ) -> int:
        """
        创建或更新知识节点。
        相同 (entity_type, entity_id, title) 视为同一节点，更新内容和嵌入。
        返回 node_id。
        """
        embedding: Optional[bytes] = None
        if auto_embed:
            embed_text = f"{entity_id} {title}\n{content[:1000]}"
            embedding = await self._emb.embed(embed_text)

        meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        exp_str = expires_at.strftime("%Y-%m-%d %H:%M:%S") if expires_at else None

        async with get_db() as db:
            # 检查是否已存在
            row = (await db.execute(
                text("""
                    SELECT id FROM claw_knowledge_nodes
                    WHERE entity_type = :et AND entity_id = :eid AND title = :title
                    LIMIT 1
                """),
                {"et": entity_type, "eid": entity_id, "title": title},
            )).mappings().first()

            if row:
                node_id = row["id"]
                await db.execute(
                    text("""
                        UPDATE claw_knowledge_nodes
                        SET content = :content,
                            source = :source,
                            importance = :importance,
                            confidence = :confidence,
                            embedding = :embedding,
                            metadata = :metadata,
                            expires_at = :expires_at,
                            updated_at = NOW()
                        WHERE id = :id
                    """),
                    {
                        "content": content,
                        "source": source,
                        "importance": importance,
                        "confidence": confidence,
                        "embedding": embedding,
                        "metadata": meta_json,
                        "expires_at": exp_str,
                        "id": node_id,
                    },
                )
            else:
                result = await db.execute(
                    text("""
                        INSERT INTO claw_knowledge_nodes
                            (entity_type, entity_id, title, content, source,
                             importance, confidence, embedding, metadata, expires_at)
                        VALUES
                            (:et, :eid, :title, :content, :source,
                             :importance, :confidence, :embedding, :metadata, :expires_at)
                    """),
                    {
                        "et": entity_type,
                        "eid": entity_id,
                        "title": title,
                        "content": content,
                        "source": source,
                        "importance": importance,
                        "confidence": confidence,
                        "embedding": embedding,
                        "metadata": meta_json,
                        "expires_at": exp_str,
                    },
                )
                node_id = result.lastrowid

            await db.commit()

        logger.debug("upsert_node: %s/%s -> id=%d", entity_type, entity_id, node_id)
        return node_id

    async def get_node(self, node_id: int) -> Optional[dict]:
        """按 id 获取节点（不含向量）。"""
        async with get_db() as db:
            row = (await db.execute(
                text("""
                    SELECT id, entity_type, entity_id, title, content,
                           source, importance, confidence, access_count,
                           last_accessed_at, metadata, created_at, updated_at, expires_at
                    FROM claw_knowledge_nodes WHERE id = :id
                """),
                {"id": node_id},
            )).mappings().first()
        return dict(row) if row else None

    async def get_entity_nodes(
        self,
        entity_id: str,
        entity_type: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        """获取某实体的所有知识节点，按重要性降序。"""
        async with get_db() as db:
            params: dict = {"eid": f"%{entity_id}%", "limit": limit}
            type_filter = "AND entity_type = :et" if entity_type else ""
            if entity_type:
                params["et"] = entity_type
            rows = (await db.execute(
                text(f"""
                    SELECT id, entity_type, entity_id, title, content,
                           source, importance, confidence, access_count,
                           last_accessed_at, metadata, created_at
                    FROM claw_knowledge_nodes
                    WHERE entity_id LIKE :eid
                      AND (expires_at IS NULL OR expires_at > NOW())
                      {type_filter}
                    ORDER BY importance DESC, last_accessed_at DESC
                    LIMIT :limit
                """),
                params,
            )).mappings().all()
        return [dict(r) for r in rows]

    async def touch_node(self, node_id: int) -> None:
        """记录访问，自适应提升重要性。"""
        async with get_db() as db:
            await db.execute(
                text("""
                    UPDATE claw_knowledge_nodes
                    SET access_count = access_count + 1,
                        last_accessed_at = NOW(),
                        importance = LEAST(100, importance + :delta)
                    WHERE id = :id
                """),
                {"delta": _IMPORTANCE_ACCESS_DELTA, "id": node_id},
            )
            await db.commit()

    async def delete_node(self, node_id: int) -> None:
        async with get_db() as db:
            await db.execute(
                text("DELETE FROM claw_knowledge_nodes WHERE id = :id"),
                {"id": node_id},
            )
            # 级联删除相关边
            await db.execute(
                text("DELETE FROM claw_knowledge_edges WHERE from_node_id = :id OR to_node_id = :id"),
                {"id": node_id},
            )
            await db.commit()

    # ══════════════════════════════════════════════════════════════
    #  图谱边 CRUD
    # ══════════════════════════════════════════════════════════════

    async def add_edge(
        self,
        from_node_id: int,
        to_node_id: int,
        relation: str,
        weight: float = 1.0,
        bidirectional: bool = False,
        evidence: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> int:
        """
        添加/更新关系边。相同 (from, to, relation) 时更新 weight。
        返回 edge_id。
        """
        meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        async with get_db() as db:
            result = await db.execute(
                text("""
                    INSERT INTO claw_knowledge_edges
                        (from_node_id, to_node_id, relation, weight, bidirectional, evidence, metadata)
                    VALUES
                        (:from_id, :to_id, :rel, :weight, :bidi, :evidence, :meta)
                    ON DUPLICATE KEY UPDATE
                        weight = :weight,
                        evidence = COALESCE(:evidence, evidence),
                        metadata = COALESCE(:meta, metadata),
                        updated_at = NOW()
                """),
                {
                    "from_id": from_node_id,
                    "to_id": to_node_id,
                    "rel": relation,
                    "weight": weight,
                    "bidi": 1 if bidirectional else 0,
                    "evidence": evidence,
                    "meta": meta_json,
                },
            )
            await db.commit()
            edge_id = result.lastrowid or 0
        return edge_id

    async def get_neighbors(
        self,
        node_id: int,
        relation: Optional[str] = None,
        depth: int = 1,
        max_nodes: int = 50,
    ) -> list[dict]:
        """
        图遍历：获取节点的 depth 跳邻居。
        返回 [{node info + relation + weight}] 列表。
        """
        visited = {node_id}
        frontier = {node_id}
        results: list[dict] = []

        for _ in range(depth):
            if not frontier or len(results) >= max_nodes:
                break
            async with get_db() as db:
                id_list = ",".join(str(i) for i in frontier)
                rel_filter = "AND e.relation = :rel" if relation else ""
                params: dict = {"ids": id_list}
                if relation:
                    params["rel"] = relation

                rows = (await db.execute(
                    text(f"""
                        SELECT n.id, n.entity_type, n.entity_id, n.title,
                               n.content, n.importance, n.source,
                               e.relation, e.weight, e.from_node_id
                        FROM claw_knowledge_edges e
                        JOIN claw_knowledge_nodes n ON (
                            (e.to_node_id = n.id AND e.from_node_id IN ({id_list}))
                            OR (e.bidirectional = 1 AND e.from_node_id = n.id AND e.to_node_id IN ({id_list}))
                        )
                        WHERE (n.expires_at IS NULL OR n.expires_at > NOW())
                          {rel_filter}
                        ORDER BY e.weight DESC, n.importance DESC
                        LIMIT :limit
                    """),
                    {**params, "limit": max_nodes - len(results)},
                )).mappings().all()

            new_frontier: set[int] = set()
            for r in rows:
                if r["id"] not in visited:
                    visited.add(r["id"])
                    new_frontier.add(r["id"])
                    results.append(dict(r))
            frontier = new_frontier

        return results

    async def get_subgraph(self, entity_id: str) -> dict:
        """
        获取以某实体为中心的子图（供前端可视化）。
        返回 {nodes: [...], edges: [...]}
        """
        async with get_db() as db:
            # 获取实体节点
            node_rows = (await db.execute(
                text("""
                    SELECT id, entity_type, entity_id, title, importance, source
                    FROM claw_knowledge_nodes
                    WHERE entity_id LIKE :eid
                      AND (expires_at IS NULL OR expires_at > NOW())
                    LIMIT 30
                """),
                {"eid": f"%{entity_id}%"},
            )).mappings().all()

            if not node_rows:
                return {"nodes": [], "edges": []}

            node_ids = [r["id"] for r in node_rows]
            id_list = ",".join(str(i) for i in node_ids)

            # 获取相关边
            edge_rows = (await db.execute(
                text(f"""
                    SELECT e.id, e.from_node_id, e.to_node_id, e.relation, e.weight,
                           fn.entity_id AS from_entity, tn.entity_id AS to_entity
                    FROM claw_knowledge_edges e
                    JOIN claw_knowledge_nodes fn ON fn.id = e.from_node_id
                    JOIN claw_knowledge_nodes tn ON tn.id = e.to_node_id
                    WHERE e.from_node_id IN ({id_list}) OR e.to_node_id IN ({id_list})
                    LIMIT 100
                """),
            )).mappings().all()

        return {
            "nodes": [dict(r) for r in node_rows],
            "edges": [dict(r) for r in edge_rows],
        }

    # ══════════════════════════════════════════════════════════════
    #  语义检索（L2 核心）
    # ══════════════════════════════════════════════════════════════

    async def semantic_search(
        self,
        query: str,
        k: int = 10,
        entity_type: Optional[str] = None,
        entity_ids: Optional[list[str]] = None,
        source: Optional[str] = None,
        min_importance: int = 0,
    ) -> list[dict]:
        """
        混合检索：
          1. SQL 关键词预过滤 → 最多 _CANDIDATE_LIMIT 个候选
          2. 若向量可用：Python-side cosine rerank
          3. 按 (importance × score) 综合排序
        返回最多 k 个节点，含 _score 字段。
        """
        # 1. 生成查询向量（可能为 None）
        query_vec = await self._emb.embed(query)

        # 2. SQL 预筛选
        async with get_db() as db:
            conditions = [
                "(expires_at IS NULL OR expires_at > NOW())",
                "importance >= :min_importance",
            ]
            params: dict[str, Any] = {
                "min_importance": min_importance,
                "limit": _CANDIDATE_LIMIT,
            }

            if entity_type:
                conditions.append("entity_type = :et")
                params["et"] = entity_type

            if source:
                conditions.append("source = :source")
                params["source"] = source

            if entity_ids:
                placeholders = ", ".join(f":eid_{i}" for i in range(len(entity_ids)))
                conditions.append(f"entity_id IN ({placeholders})")
                for i, eid in enumerate(entity_ids):
                    params[f"eid_{i}"] = eid

            # 关键词过滤（FTS 替代方案，无需全文索引）
            if query and len(query) >= 2:
                kw_conditions = []
                for i, token in enumerate(_tokenize(query)):
                    kw_conditions.append(f"(title LIKE :kw_{i} OR content LIKE :kw_{i} OR entity_id LIKE :kw_{i})")
                    params[f"kw_{i}"] = f"%{token}%"
                if kw_conditions:
                    conditions.append(f"({' OR '.join(kw_conditions)})")

            where_clause = " AND ".join(conditions)

            rows = (await db.execute(
                text(f"""
                    SELECT id, entity_type, entity_id, title, content,
                           source, importance, confidence, embedding,
                           access_count, last_accessed_at, metadata, created_at
                    FROM claw_knowledge_nodes
                    WHERE {where_clause}
                    ORDER BY importance DESC, last_accessed_at DESC
                    LIMIT :limit
                """),
                params,
            )).mappings().all()

        candidates = [dict(r) for r in rows]

        # 3. 向量重排
        if query_vec is not None:
            for c in candidates:
                raw_emb = c.get("embedding")
                sim = EmbeddingService.cosine_similarity(query_vec, raw_emb)
                # 综合分 = 语义相似度 × 重要性权重
                c["_score"] = sim * (0.5 + c["importance"] / 200.0)
            candidates.sort(key=lambda x: x["_score"], reverse=True)
        else:
            # 无向量时按重要性排序
            for c in candidates:
                c["_score"] = c["importance"] / 100.0

        # 4. 清理二进制字段，返回可序列化结果
        for c in candidates:
            c.pop("embedding", None)
            if isinstance(c.get("metadata"), str):
                try:
                    c["metadata"] = json.loads(c["metadata"])
                except Exception:
                    pass

        return candidates[:k]

    # ══════════════════════════════════════════════════════════════
    #  档案摘要（L4）
    # ══════════════════════════════════════════════════════════════

    async def save_summary(
        self,
        entity_type: str,
        entity_id: str,
        period: str,
        period_start: str,
        period_end: str,
        summary_text: str,
        key_events: Optional[list] = None,
        key_facts: Optional[list] = None,
        metadata: Optional[dict] = None,
        auto_embed: bool = True,
    ) -> int:
        """保存周期压缩摘要，相同 (entity_type, entity_id, period, period_start) 时更新。"""
        embedding: Optional[bytes] = None
        if auto_embed:
            embedding = await self._emb.embed(f"{entity_id} {period_start}\n{summary_text[:1000]}")

        async with get_db() as db:
            result = await db.execute(
                text("""
                    INSERT INTO claw_memory_summaries
                        (entity_type, entity_id, period, period_start, period_end,
                         summary_text, key_events, key_facts, embedding, metadata)
                    VALUES
                        (:et, :eid, :period, :ps, :pe, :summary, :events, :facts, :emb, :meta)
                    ON DUPLICATE KEY UPDATE
                        summary_text = :summary,
                        key_events = :events,
                        key_facts = :facts,
                        embedding = :emb,
                        metadata = :meta
                """),
                {
                    "et": entity_type,
                    "eid": entity_id,
                    "period": period,
                    "ps": period_start,
                    "pe": period_end,
                    "summary": summary_text,
                    "events": json.dumps(key_events or [], ensure_ascii=False),
                    "facts": json.dumps(key_facts or [], ensure_ascii=False),
                    "emb": embedding,
                    "meta": json.dumps(metadata or {}, ensure_ascii=False),
                },
            )
            await db.commit()
        return result.lastrowid or 0

    async def get_summaries(
        self,
        entity_id: str,
        entity_type: str = "person",
        period: str = "weekly",
        limit: int = 10,
    ) -> list[dict]:
        """获取某实体的历史摘要（不含向量）。"""
        async with get_db() as db:
            rows = (await db.execute(
                text("""
                    SELECT entity_type, entity_id, period, period_start, period_end,
                           summary_text, key_events, key_facts, metadata, created_at
                    FROM claw_memory_summaries
                    WHERE entity_type = :et AND entity_id LIKE :eid AND period = :period
                    ORDER BY period_start DESC
                    LIMIT :limit
                """),
                {"et": entity_type, "eid": f"%{entity_id}%", "period": period, "limit": limit},
            )).mappings().all()
        return [dict(r) for r in rows]

    # ══════════════════════════════════════════════════════════════
    #  统计
    # ══════════════════════════════════════════════════════════════

    async def stats(self) -> dict:
        """知识库统计信息。"""
        async with get_db() as db:
            node_counts = (await db.execute(
                text("""
                    SELECT entity_type, COUNT(*) AS cnt,
                           AVG(importance) AS avg_importance,
                           SUM(access_count) AS total_accesses
                    FROM claw_knowledge_nodes
                    WHERE expires_at IS NULL OR expires_at > NOW()
                    GROUP BY entity_type
                """)
            )).mappings().all()

            edge_count = (await db.execute(
                text("SELECT COUNT(*) AS cnt FROM claw_knowledge_edges")
            )).scalar()

            summary_count = (await db.execute(
                text("SELECT COUNT(*) AS cnt FROM claw_memory_summaries")
            )).scalar()

            embed_coverage = (await db.execute(
                text("""
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN embedding IS NOT NULL THEN 1 ELSE 0 END) AS embedded
                    FROM claw_knowledge_nodes
                    WHERE expires_at IS NULL OR expires_at > NOW()
                """)
            )).mappings().first()

        embed_total = embed_coverage["total"] or 0
        embed_done = embed_coverage["embedded"] or 0

        return {
            "nodes_by_type": [dict(r) for r in node_counts],
            "total_edges": edge_count or 0,
            "total_summaries": summary_count or 0,
            "embedding_coverage": {
                "total": embed_total,
                "embedded": embed_done,
                "coverage_pct": round(embed_done / embed_total * 100, 1) if embed_total else 0,
            },
        }


# ── 工具函数 ────────────────────────────────────────────────────

def _tokenize(query: str) -> list[str]:
    """
    简单分词：按空格/标点切割，过滤长度 < 2 的词。
    中文语境下适合按 2-gram 或直接整句 LIKE 匹配。
    """
    import re
    tokens = re.split(r"[\s，。！？、；：「」【】\(\)\[\]]+", query.strip())
    seen: set[str] = set()
    result: list[str] = []
    for t in tokens:
        if len(t) >= 2 and t not in seen:
            seen.add(t)
            result.append(t)
    # 最多 5 个 token 防止查询过慢
    return result[:5]
