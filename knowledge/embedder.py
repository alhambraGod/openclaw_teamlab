"""
OpenClaw TeamLab — Embedding Service
向量嵌入服务，基于 OpenAI-compatible API

设计要点：
  - 复用已有 LLM_BASE_URL / LLM_API_KEY，无需额外配置
  - 优先使用 text-embedding-3-small (1536 dims, 6 KB / vector)
  - 首次调用时探测可用性，后续直接走缓存结论
  - 向量以 float32 binary (struct pack) 存储于 MySQL MEDIUMBLOB
  - 降级策略：embedding 不可用时返回 None，系统退化为关键词检索
  - Python-side cosine similarity（无需向量数据库扩展）
"""
from __future__ import annotations

import logging
import math
import struct
from typing import Optional

import openai

from config.settings import settings

logger = logging.getLogger("teamlab.knowledge.embedder")

# OpenAI text-embedding-3-small: 1536 dims
EMBED_MODEL = "text-embedding-3-small"
EMBED_DIMS = 1536
EMBED_BYTES = EMBED_DIMS * 4  # float32 = 4 bytes each


class EmbeddingService:
    """
    轻量级嵌入服务。

    用法：
        svc = EmbeddingService()
        vec_bytes = await svc.embed("张三是NLP方向的博士生")
        score = svc.cosine_similarity(vec_bytes_a, vec_bytes_b)
    """

    def __init__(self) -> None:
        self._client = openai.AsyncOpenAI(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY or "sk-placeholder",
        )
        self._available: Optional[bool] = None  # 懒检测

    # ── 公共接口 ──────────────────────────────────────────────────

    async def embed(self, text: str) -> Optional[bytes]:
        """
        将文本转为 float32 向量并以 bytes 返回（可直接存入 MySQL BLOB）。
        不可用时返回 None（退化为关键词检索）。
        """
        if not await self._check_available():
            return None
        try:
            text = _truncate(text, 8000)
            resp = await self._client.embeddings.create(
                input=text, model=EMBED_MODEL
            )
            return _floats_to_bytes(resp.data[0].embedding)
        except Exception as exc:
            logger.warning("embed failed: %s", exc)
            return None

    async def embed_batch(self, texts: list[str]) -> list[Optional[bytes]]:
        """批量嵌入（单次 API 调用，降低延迟）。"""
        if not await self._check_available():
            return [None] * len(texts)
        try:
            truncated = [_truncate(t, 8000) for t in texts]
            resp = await self._client.embeddings.create(
                input=truncated, model=EMBED_MODEL
            )
            # 结果顺序与输入一致
            ordered = sorted(resp.data, key=lambda d: d.index)
            return [_floats_to_bytes(d.embedding) for d in ordered]
        except Exception as exc:
            logger.warning("embed_batch failed: %s", exc)
            return [None] * len(texts)

    @staticmethod
    def cosine_similarity(a: Optional[bytes], b: Optional[bytes]) -> float:
        """两向量余弦相似度，任意一方为 None 则返回 0.0。"""
        if a is None or b is None:
            return 0.0
        va = _bytes_to_floats(a)
        vb = _bytes_to_floats(b)
        if len(va) != len(vb) or not va:
            return 0.0
        dot = sum(x * y for x, y in zip(va, vb))
        mag_a = math.sqrt(sum(x * x for x in va))
        mag_b = math.sqrt(sum(x * x for x in vb))
        denom = mag_a * mag_b
        return dot / denom if denom > 1e-9 else 0.0

    @staticmethod
    def top_k_by_similarity(
        query_vec: Optional[bytes],
        candidates: list[dict],
        vec_key: str = "embedding",
        k: int = 10,
    ) -> list[dict]:
        """
        从 candidates 列表中取最相似的 k 个。
        candidates 中每项需包含 vec_key 字段（bytes 或 None）。
        返回列表已按 _score 降序排列，并附加 _score 字段。
        """
        if query_vec is None:
            # 无向量，原样返回（前 k 个）
            for c in candidates:
                c.setdefault("_score", 0.5)
            return candidates[:k]

        scored = []
        for item in candidates:
            vec = item.get(vec_key)
            score = EmbeddingService.cosine_similarity(query_vec, vec)
            scored.append({**item, "_score": score})

        scored.sort(key=lambda x: x["_score"], reverse=True)
        return scored[:k]

    async def is_available(self) -> bool:
        return await self._check_available()

    # ── 私有方法 ──────────────────────────────────────────────────

    async def _check_available(self) -> bool:
        """探测 embedding API 是否可用（仅首次调用时探测）。"""
        if self._available is not None:
            return self._available
        try:
            await self._client.embeddings.create(
                input="ping", model=EMBED_MODEL
            )
            self._available = True
            logger.info("Embedding service available: %s / %s", settings.LLM_BASE_URL, EMBED_MODEL)
        except Exception as exc:
            self._available = False
            logger.warning(
                "Embedding service unavailable (%s). Falling back to keyword-only retrieval.", exc
            )
        return self._available


# ── 全局单例（lazy init）────────────────────────────────────────

_embedder: Optional[EmbeddingService] = None


def get_embedder() -> EmbeddingService:
    global _embedder
    if _embedder is None:
        _embedder = EmbeddingService()
    return _embedder


# ── 工具函数 ────────────────────────────────────────────────────

def _floats_to_bytes(floats: list[float]) -> bytes:
    """float list → 小端 float32 bytes（存 MySQL BLOB）。"""
    return struct.pack(f"<{len(floats)}f", *floats)


def _bytes_to_floats(data: bytes) -> list[float]:
    """小端 float32 bytes → float list。"""
    n = len(data) // 4
    return list(struct.unpack(f"<{n}f", data[:n * 4]))


def _truncate(text: str, max_chars: int) -> str:
    """嵌入模型有 token 上限，粗略按字符数截断。"""
    return text[:max_chars] if len(text) > max_chars else text
