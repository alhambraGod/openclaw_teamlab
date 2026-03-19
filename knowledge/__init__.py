"""
OpenClaw TeamLab — Knowledge Layer
分层知识存储与检索系统

Architecture:
    L0  Working Memory  → Redis  (TTL-based, in-flight context)
    L1  Episodic Memory → task_log + conversations  (已有)
    L2  Semantic Memory → knowledge_nodes  (向量嵌入 + 语义检索)
    L3  Structural Mem  → knowledge_edges  (知识图谱 + 关系遍历)
    L4  Archival Memory → memory_summaries (周期压缩蒸馏)
    L5  Session State   → memory_sessions  (跨会话工作记忆)
"""

from knowledge.embedder import EmbeddingService
from knowledge.store import KnowledgeStore
from knowledge.retriever import KnowledgeRetriever
from knowledge.memory import MemoryManager

__all__ = [
    "EmbeddingService",
    "KnowledgeStore",
    "KnowledgeRetriever",
    "MemoryManager",
]
