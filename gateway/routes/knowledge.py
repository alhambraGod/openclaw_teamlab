"""
OpenClaw TeamLab — Knowledge API Routes
知识图谱管理接口

GET  /api/knowledge/search          — 语义 + 关键词检索
GET  /api/knowledge/entity/{id}     — 实体知识画像
GET  /api/knowledge/graph/{id}      — 实体子图（可视化数据）
GET  /api/knowledge/stats           — 知识库统计
POST /api/knowledge/nodes           — 手动写入知识节点
DELETE /api/knowledge/nodes/{id}    — 删除节点
GET  /api/knowledge/memory/{sk}     — 获取会话工作记忆
POST /api/knowledge/memory/{sk}/facts — 向会话工作记忆添加事实
DELETE /api/knowledge/memory/{sk}   — 清除工作记忆
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from knowledge.store import KnowledgeStore
from knowledge.retriever import KnowledgeRetriever
from knowledge.memory import MemoryManager

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
logger = logging.getLogger("teamlab.gateway.knowledge")

_store = KnowledgeStore()
_retriever = KnowledgeRetriever(_store)
_memory = MemoryManager()


# ── Pydantic 模型 ─────────────────────────────────────────────────

class NodeCreate(BaseModel):
    entity_type: str = Field(..., description="person|project|concept|research|insight|event")
    entity_id: str = Field(..., description="实体唯一标识（人名、项目名等）")
    title: str = Field(..., description="知识点标题")
    content: str = Field(..., description="完整知识文本")
    source: str = Field("manual", description="来源标识")
    importance: int = Field(50, ge=0, le=100, description="重要性 0-100")
    metadata: Optional[dict] = None


class FactAdd(BaseModel):
    fact: str = Field(..., description="要添加到工作记忆的事实")
    pin: bool = Field(False, description="是否固定（跨会话保留）")
    user_id: str = Field("unknown", description="用户 ID")


# ── 检索接口 ──────────────────────────────────────────────────────

@router.get("/search")
async def search_knowledge(
    q: str = Query(..., description="查询文本"),
    entity_type: Optional[str] = Query(None, description="按类型过滤"),
    k: int = Query(10, ge=1, le=30, description="返回数量"),
):
    """
    语义 + 关键词混合检索知识节点。
    向量可用时优先语义排序，降级时按关键词 + 重要性排序。
    """
    try:
        results = await _store.semantic_search(
            query=q,
            k=k,
            entity_type=entity_type,
        )
        return {"query": q, "count": len(results), "results": results}
    except Exception as exc:
        logger.error("Knowledge search error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/entity/{entity_id}")
async def get_entity_profile(
    entity_id: str,
    entity_type: Optional[str] = Query(None),
):
    """获取实体完整知识画像（节点 + 关系 + 摘要）。"""
    try:
        profile = await _retriever.retrieve_entity_profile(entity_id)
        nodes = await _store.get_entity_nodes(entity_id, entity_type)
        return {
            "entity_id": entity_id,
            "profile_markdown": profile,
            "nodes": nodes,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/graph/{entity_id}")
async def get_entity_graph(entity_id: str):
    """
    获取以某实体为中心的知识子图（节点 + 边）。
    适合前端 force-graph 或 d3 可视化。
    """
    try:
        subgraph = await _store.get_subgraph(entity_id)
        return subgraph
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/stats")
async def get_knowledge_stats():
    """知识库整体统计：节点数、边数、嵌入覆盖率等。"""
    try:
        stats = await _store.stats()
        return stats
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── 写入接口 ──────────────────────────────────────────────────────

@router.post("/nodes", status_code=201)
async def create_node(body: NodeCreate):
    """手动写入知识节点（自动生成向量嵌入）。"""
    try:
        node_id = await _store.upsert_node(
            entity_type=body.entity_type,
            entity_id=body.entity_id,
            title=body.title,
            content=body.content,
            source=body.source,
            importance=body.importance,
            metadata=body.metadata,
        )
        return {"node_id": node_id, "message": "知识节点已创建/更新"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/nodes/{node_id}")
async def delete_node(node_id: int):
    """删除知识节点及其所有关联边。"""
    try:
        await _store.delete_node(node_id)
        return {"message": f"节点 {node_id} 已删除"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── 工作记忆接口 ──────────────────────────────────────────────────

@router.get("/memory/{session_key:path}")
async def get_session_memory(session_key: str, user_id: str = Query("unknown")):
    """获取用户会话工作记忆。session_key 如 'web:pi' 或 'feishu:xxx'。"""
    try:
        session = await _memory.get_session(session_key, user_id)
        formatted = await _memory.format_working_memory(session_key, user_id)
        return {
            "session_key": session_key,
            "session": session,
            "formatted_markdown": formatted,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/memory/{session_key:path}/facts")
async def add_memory_fact(session_key: str, body: FactAdd):
    """向用户工作记忆添加事实（pin=true 则跨会话固定）。"""
    try:
        await _memory.add_fact(
            session_key=session_key,
            fact=body.fact,
            user_id=body.user_id,
            pin=body.pin,
        )
        return {"message": "事实已添加到工作记忆", "pinned": body.pin}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/memory/{session_key:path}")
async def clear_session_memory(
    session_key: str,
    user_id: str = Query("unknown"),
    keep_pinned: bool = Query(True),
):
    """清除会话工作记忆（默认保留固定事实）。"""
    try:
        await _memory.clear_working_memory(session_key, user_id, keep_pinned)
        return {"message": "工作记忆已清除", "kept_pinned": keep_pinned}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
