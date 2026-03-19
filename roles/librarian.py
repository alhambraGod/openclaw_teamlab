"""
OpenClaw TeamLab — Librarian Role（知识管理者）

职责：
  1. 扫描近期对话（未被处理的问答对）
  2. 用 LLM 提取团队知识：成员特征、项目进展、研究洞见、师生关系
  3. 将结构化知识写入 claw_knowledge_nodes（L2 语义记忆）+ claw_knowledge_edges（L3 图谱）
  4. 同时向后兼容写入 claw_pi_agent_insights（type=team_knowledge）
  5. 标记对话为"已提取"，避免重复处理
  6. 【核心】调用 CoevoKnowledgeSync 将 cognalign_coevo_prod 最新数据增量同步到知识图谱
     ——会议报告（导师点评、学生总结）、研究规划、协作推荐、Agent 记忆
     确保系统进化始终基于 coevo 的最新事实真相，而非仅靠对话历史推断。

这些知识会在下次 pi_agent 处理查询时被 KnowledgeRetriever 检索注入，
使系统越来越懂团队。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from config.database import get_db
from roles.base import AutonomousRole

logger = logging.getLogger("teamlab.roles.librarian")

# 每次最多处理的对话轮次（避免一次运行太久）
BATCH_SIZE = 50

EXTRACT_SYSTEM = """你是一个专业的学术团队知识管理助手。
你的任务是从PI（导师）助手与用户的对话中，提取有价值的团队知识片段。

提取规则：
1. 只提取关于真实团队成员、项目、研究方向的具体信息
2. 不提取泛化建议或通用知识
3. 每条知识要有具体的主体（人名/项目名）
4. 以JSON数组返回，每条格式：
   {"subject": "主体名称", "fact": "具体事实描述", "type": "person|project|research|collaboration"}

如无有价值信息，返回空数组 []。"""


class Librarian(AutonomousRole):
    """知识管理者：从对话历史中提取并积累团队知识。"""

    name = "librarian"
    description = "从对话中提取团队知识，构建持久记忆，让系统越来越懂PI团队"

    async def run(self) -> dict:
        logger.info("[Librarian] Starting knowledge extraction run")
        start = datetime.now(timezone.utc)

        # ── 阶段一：从 claw_task_log 对话历史提取知识 ──────────────
        pairs = await self._fetch_unprocessed_pairs()
        total_facts = 0
        processed_ids: list[int] = []

        if pairs:
            logger.info("[Librarian] Found %d conversation pairs to process", len(pairs))

            # 知识图谱存储层（L2 + L3）
            try:
                from knowledge.store import KnowledgeStore
                ks = KnowledgeStore()
            except Exception as _ks_err:
                logger.warning("[Librarian] KnowledgeStore init failed: %s — falling back to claw_pi_agent_insights only", _ks_err)
                ks = None

            for pair in pairs:
                facts = await self._extract_facts(pair)
                if facts:
                    node_ids: dict[str, int] = {}  # subject → node_id，用于建边

                    for fact in facts:
                        subject = fact.get("subject", "团队")
                        content = fact.get("fact", "")
                        fact_type = fact.get("type", "other")

                        # 写入 claw_knowledge_nodes（L2）
                        if ks is not None:
                            try:
                                entity_type = _map_fact_type(fact_type)
                                node_id = await ks.upsert_node(
                                    entity_type=entity_type,
                                    entity_id=subject,
                                    title=_make_title(content),
                                    content=content,
                                    source="librarian",
                                    importance=_score_importance(fact_type),
                                    metadata={
                                        "fact_type": fact_type,
                                        "source_conv_id": pair.get("conv_id"),
                                        "extracted_at": start.isoformat(),
                                    },
                                )
                                node_ids[subject] = node_id
                            except Exception as _ne:
                                logger.debug("[Librarian] claw_knowledge_nodes write failed: %s", _ne)

                        # 向后兼容写入 claw_pi_agent_insights
                        await self.save_insight(
                            insight_type="team_knowledge",
                            subject=subject,
                            content=content,
                            metadata={
                                "fact_type": fact_type,
                                "source_conv_id": pair.get("conv_id"),
                                "extracted_at": start.isoformat(),
                            },
                        )
                        total_facts += 1

                    # 同一对话中多实体建立关系边（L3）
                    if ks is not None and len(node_ids) >= 2:
                        subjects = list(node_ids.keys())
                        for i in range(len(subjects) - 1):
                            for j in range(i + 1, len(subjects)):
                                try:
                                    await ks.add_edge(
                                        from_node_id=node_ids[subjects[i]],
                                        to_node_id=node_ids[subjects[j]],
                                        relation="co_mentioned",
                                        weight=0.5,
                                        bidirectional=True,
                                        evidence=f"同时出现于对话 {pair.get('conv_id')}",
                                    )
                                except Exception:
                                    pass

                processed_ids.append(pair.get("conv_id"))

            await self._mark_processed(processed_ids)
            logger.info("[Librarian] Phase 1: Extracted %d facts from %d conversations", total_facts, len(pairs))
        else:
            logger.info("[Librarian] Phase 1: No new conversations to process")

        # ── 阶段二：从 cognalign_coevo_prod 增量同步最新数据 ────────
        # 这是确保系统进化基于真实数据（而非仅靠对话推断）的核心步骤
        coevo_stats: dict = {}
        try:
            from data_bridge.coevo_knowledge_sync import CoevoKnowledgeSync
            logger.info("[Librarian] Phase 2: Syncing fresh data from cognalign_coevo_prod")
            coevo_result = await CoevoKnowledgeSync().run()
            coevo_stats = coevo_result
            logger.info(
                "[Librarian] Phase 2: coevo sync complete — reports=%d plans=%d collabs=%d memories=%d",
                coevo_result.get("reports", 0),
                coevo_result.get("plans", 0),
                coevo_result.get("collabs", 0),
                coevo_result.get("memories", 0),
            )
        except Exception as exc:
            logger.error("[Librarian] Phase 2: coevo sync failed: %s", exc, exc_info=True)
            coevo_stats = {"error": str(exc)}

        duration_s = (datetime.now(timezone.utc) - start).total_seconds()

        # ── 阶段三：生成本次运行摘要 ──────────────────────────────────
        coevo_nodes = coevo_stats.get("total_nodes", 0)
        if total_facts > 0 or coevo_nodes > 0:
            summary = await self._generate_summary(total_facts, len(pairs), coevo_stats)
            await self.save_insight(
                insight_type="system_evolution",
                subject="Librarian 知识提取报告",
                content=summary,
                metadata={
                    "role": "librarian",
                    "facts_extracted": total_facts,
                    "conversations_processed": len(pairs),
                    "coevo_nodes_synced": coevo_nodes,
                    "run_at": start.isoformat(),
                },
            )

        logger.info("[Librarian] All phases complete in %.1fs", duration_s)
        return {
            "status": "completed",
            "processed": len(pairs),
            "facts_extracted": total_facts,
            "coevo_sync": coevo_stats,
            "duration_seconds": round(duration_s, 1),
        }

    async def _fetch_unprocessed_pairs(self) -> list[dict]:
        """获取未提取知识的对话对（user 问 + assistant 答）。"""
        try:
            async with get_db() as db:
                # 获取最近完成的任务日志（有 result_summary），并排除 scheduler 来源
                rows = (await db.execute(
                    text("""
                        SELECT
                            tl.id      AS conv_id,
                            tl.user_id,
                            tl.input_text  AS question,
                            tl.result_summary AS answer,
                            tl.skill_used,
                            tl.created_at
                        FROM claw_task_log tl
                        WHERE tl.status = 'completed'
                          AND tl.result_summary IS NOT NULL
                          AND tl.source != 'scheduler'
                          AND (tl.librarian_processed IS NULL OR tl.librarian_processed = 0)
                        ORDER BY tl.created_at DESC
                        LIMIT :batch
                    """),
                    {"batch": BATCH_SIZE},
                )).mappings().all()
                return [dict(r) for r in rows]
        except Exception as exc:
            # librarian_processed 列可能不存在，回退到最近50条
            logger.warning("[Librarian] Fetch failed: %s — falling back to recent tasks", exc)
            try:
                async with get_db() as db:
                    rows = (await db.execute(
                        text("""
                            SELECT id AS conv_id, user_id, input_text AS question,
                                   result_summary AS answer, skill_used, created_at
                            FROM claw_task_log
                            WHERE status = 'completed'
                              AND result_summary IS NOT NULL
                              AND source != 'scheduler'
                            ORDER BY created_at DESC
                            LIMIT :batch
                        """),
                        {"batch": BATCH_SIZE},
                    )).mappings().all()
                    return [dict(r) for r in rows]
            except Exception as exc2:
                logger.error("[Librarian] Fallback fetch also failed: %s", exc2)
                return []

    async def _extract_facts(self, pair: dict) -> list[dict]:
        """使用 LLM 从一个问答对中提取团队知识。"""
        question = pair.get("question", "")
        answer = pair.get("answer", "")
        if not question or not answer:
            return []

        # 过滤太短的回答（可能是错误消息）
        if len(answer) < 50:
            return []

        prompt = f"""以下是一段 PI 助手的问答对话，请提取其中的团队知识：

用户问题：{question[:500]}

AI 回答：{answer[:1500]}

请按要求提取知识并以JSON数组格式返回。"""

        try:
            raw = await self.llm_call(
                prompt=prompt,
                system=EXTRACT_SYSTEM,
                max_tokens=800,
                temperature=0.2,
            )
            # 解析 JSON
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1].lstrip("json").strip()
            facts = json.loads(raw)
            if isinstance(facts, list):
                return [f for f in facts if isinstance(f, dict) and f.get("fact")]
        except Exception as exc:
            logger.debug("[Librarian] Extract failed for conv %s: %s", pair.get("conv_id"), exc)

        return []

    async def _mark_processed(self, conv_ids: list[int]) -> None:
        """尝试标记对话为已处理（如果列不存在则静默失败）。"""
        if not conv_ids:
            return
        try:
            async with get_db() as db:
                await db.execute(
                    text("UPDATE claw_task_log SET librarian_processed=1 WHERE id IN :ids"),
                    {"ids": tuple(conv_ids)},
                )
        except Exception:
            pass  # 列不存在时静默忽略

    async def _generate_summary(self, fact_count: int, conv_count: int, coevo_stats: dict | None = None) -> str:
        """生成本次知识提取的摘要。"""
        try:
            recent = await self.get_recent_insights("team_knowledge", days=1, limit=10)
            subjects = list({r.get("subject", "") for r in recent if r.get("subject")})
            subject_str = "、".join(subjects[:8]) if subjects else "（暂无）"

            # 追加知识图谱统计
            try:
                from knowledge.store import KnowledgeStore
                ks = KnowledgeStore()
                stats = await ks.stats()
                total_nodes = sum(t["cnt"] for t in stats.get("nodes_by_type", []))
                embed_pct = stats.get("embedding_coverage", {}).get("coverage_pct", 0)
                graph_info = (
                    f"\n知识图谱状态：{total_nodes} 个知识节点，"
                    f"{stats.get('total_edges', 0)} 条关系边，"
                    f"向量覆盖率 {embed_pct}%。"
                )
            except Exception:
                graph_info = ""

            # coevo 同步统计
            coevo_info = ""
            if coevo_stats and coevo_stats.get("total_nodes", 0) > 0:
                coevo_info = (
                    f"\n\n**CoEvo 数据同步**（来源：cognalign_coevo_prod）：\n"
                    f"- 会议报告：{coevo_stats.get('reports', 0)} 条\n"
                    f"- 研究规划：{coevo_stats.get('plans', 0)} 条\n"
                    f"- 协作推荐：{coevo_stats.get('collabs', 0)} 条\n"
                    f"- Agent 记忆：{coevo_stats.get('memories', 0)} 条\n"
                    f"共写入 {coevo_stats.get('total_nodes', 0)} 个知识节点（增量更新）"
                )

            return (
                f"本次知识提取运行：从 {conv_count} 条对话中提取了 {fact_count} 条团队知识。\n"
                f"涉及主体：{subject_str}\n"
                f"这些知识已写入知识图谱（L2 语义记忆），后续对话将通过混合检索自动注入。"
                f"{graph_info}"
                f"{coevo_info}"
            )
        except Exception:
            coevo_total = coevo_stats.get("total_nodes", 0) if coevo_stats else 0
            return (
                f"知识管理者完成运行：处理 {conv_count} 条对话，提取 {fact_count} 条知识；"
                f"从 CoEvo 同步 {coevo_total} 个知识节点。"
            )


# ── 工具函数 ────────────────────────────────────────────────────

def _map_fact_type(fact_type: str) -> str:
    """将 Librarian 提取的 fact_type 映射到 claw_knowledge_nodes.entity_type。"""
    return {
        "person": "person",
        "project": "project",
        "research": "research",
        "collaboration": "person",
    }.get(fact_type, "insight")


def _score_importance(fact_type: str) -> int:
    """根据事实类型赋予初始重要性分数。"""
    return {
        "person": 65,
        "project": 70,
        "research": 60,
        "collaboration": 75,
    }.get(fact_type, 50)


def _make_title(content: str, max_len: int = 60) -> str:
    """从内容文本生成简短标题。"""
    first_line = content.split("\n")[0].strip()
    if len(first_line) <= max_len:
        return first_line
    return first_line[:max_len - 1] + "…"
