# Knowledge System — 分层知识存储与检索

本文档记录 OpenClaw TeamLab 的知识系统设计，覆盖存储架构、数据流、检索策略与关键工作流程，供系统学习、总结与后续演进参考。

---

## 设计动机

系统初期的知识存储方案（`pi_agent_insights` 平铺文本 + 关键词过滤）存在以下瓶颈：

| 问题 | 表现 |
|------|------|
| 检索精度低 | 只能精确字符串匹配，语义相近但字面不同的知识无法命中 |
| 无关系建模 | 人 → 项目、人 → 人等关联信息丢失，无法做图遍历 |
| 记忆不持久 | 每次对话都从空白开始，无法利用历史积累 |
| 知识无结构 | 所有洞见混在一张表，无法按实体类型差异化处理 |

新架构参考了 **MemGPT**（分层记忆管理）、**A-MEM**（自主记忆更新）、**Zep**（会话记忆持久化）的设计理念，结合项目约束（MySQL + Redis，不引入专用向量数据库）实现了务实可扩展的分层方案。

---

## 分层架构

```
┌──────────────────────────────────────────────────────────┐
│  L0  Working Memory   Redis TTL (1h)                     │
│       飞行中上下文 / ETA 统计 / 活跃任务状态               │
├──────────────────────────────────────────────────────────┤
│  L1  Episodic Memory  MySQL — claw_task_log + claw_conversations   │
│       完整对话历史 / 任务执行记录 / 时序事件               │
├──────────────────────────────────────────────────────────┤
│  L2  Semantic Memory  MySQL — claw_knowledge_nodes            │
│       知识原子 + float32[1536] 向量嵌入                   │
│       支持语义相似度检索（Python-side cosine）            │
├──────────────────────────────────────────────────────────┤
│  L3  Structural Memory MySQL — claw_knowledge_edges           │
│       实体间有向关系图（协作/指导/研究方向等）             │
│       支持图遍历（BFS 1-hop / 2-hop）                    │
├──────────────────────────────────────────────────────────┤
│  L4  Archival Memory  MySQL — claw_memory_summaries           │
│       实体知识的周期压缩蒸馏（日/周/月摘要）               │
│       带向量嵌入，支持跨时段语义回溯                      │
├──────────────────────────────────────────────────────────┤
│  L5  Session State    Redis + MySQL — claw_memory_sessions    │
│       per-user 工作记忆：working_facts / pinned_facts    │
│       跨会话延续，MemGPT inner_context 风格               │
└──────────────────────────────────────────────────────────┘
```

**L0–L1** 已有实现，**L2–L5** 为新增分层记忆系统（Migration 003）。

---

## 核心模块

### EmbeddingService（`knowledge/embedder.py`）

向量嵌入服务，复用项目已有的 LLM 代理端点，无需额外配置。

```python
# 核心能力
svc = EmbeddingService()
vec_bytes = await svc.embed("张三研究 RAG 系统优化")  # -> bytes | None
score = svc.cosine_similarity(vec_a, vec_b)           # -> float 0.0-1.0
ranked = svc.top_k_by_similarity(query_vec, candidates, k=10)
```

**关键设计**：
- 模型：`text-embedding-3-small`（1536 维，6144 字节/向量）
- 存储：`struct.pack('<1536f', ...)` 写入 MySQL MEDIUMBLOB，无需扩展插件
- 降级：API 不可用时 `embed()` 返回 `None`，系统自动退化为关键词检索，功能不中断
- 批量接口：`embed_batch(texts)` 单次 API 调用，降低延迟

### KnowledgeStore（`knowledge/store.py`）

L2/L3/L4 的统一 CRUD 层，单一职责：持久化读写，不含 LLM 调用。

**节点操作**：
```python
ks = KnowledgeStore()

# 幂等写入（相同 entity_type + entity_id + title 时更新内容和嵌入）
node_id = await ks.upsert_node(
    entity_type="person",
    entity_id="张三",
    title="RAG 系统优化研究进展",
    content="张三目前重点研究检索增强生成的索引效率...",
    importance=65,
)

# 建立关系边
await ks.add_edge(node_a, node_b, relation="collaborates_with", weight=0.8)

# 语义检索（向量重排 + 重要性加权）
results = await ks.semantic_search("张三研究方向", k=10)
```

**自适应重要性**：节点每被访问一次，`importance` 自动 +2（上限 100），使频繁访问的知识优先召回。

### KnowledgeRetriever（`knowledge/retriever.py`）

面向 LLM 上下文的四步检索管线，输出可直接注入 system prompt 的 Markdown。

```
输入：query 文本 + session_entities
  │
  Step 1  实体提取   识别人名、项目名（正则 + 停用词过滤）
  │
  Step 2  语义检索   SQL 预筛 200 候选 → Python cosine rerank
  │                  综合分 = cosine × (0.5 + importance/200)
  │
  Step 3  图谱扩展   Top-3 节点的 1-hop 邻居（补充关联实体上下文）
  │
  Step 4  档案补充   知识不足时追加 L4 历史摘要
  │
  输出：Markdown 上下文（≤4000 字符，按类型分组，含来源标记）
```

降级策略：
- 无向量 → 关键词检索 + 重要性排序
- 无知识图谱数据 → 回退到旧版 `pi_agent_insights` 平铺查询

### MemoryManager（`knowledge/memory.py`）

MemGPT 风格的工作记忆管理，解决 HTTP 无状态下的跨会话记忆问题。

```python
mm = MemoryManager()

# 获取会话（Redis 优先，缺失则从 MySQL 回填）
session = await mm.get_session("web:pi", "user_123")

# 添加事实（溢出时自动压缩最老的事实到 claw_knowledge_nodes）
await mm.add_fact("web:pi", "张三上周提交了 ICML 论文", pin=False)

# PIN 重要事实（跨会话永久保留）
await mm.add_fact("feishu:xxx", "PI 要求每周三下午开组会", pin=True)

# 格式化注入 LLM
working_mem_ctx = await mm.format_working_memory("web:pi")
```

**溢出压缩**：`working_facts` 超过 20 条时，最老的条目自动蒸馏写入 `knowledge_nodes`（L2），避免工作记忆无限增长，同时不丢失历史信息。

---

## 关键工作流程

### 流程一：知识写入（Librarian 每日自动运行）

```
凌晨 01:00  Scheduler 触发 __librarian__ 任务
    │
    ├─ 扫描 claw_task_log（status=completed, source≠scheduler, librarian_processed IS NULL）
    │   每批最多 50 条
    │
    ├─ 对每个问答对：
    │   └─ LLM 提取 → [{subject: "张三", fact: "...", type: "person"}, ...]
    │
    ├─ 写入 claw_knowledge_nodes（L2）：
    │   └─ 自动生成向量嵌入 → 存入 embedding BLOB
    │
    ├─ 建立 claw_knowledge_edges（L3）：
    │   └─ 同一对话中的多个实体 → co_mentioned 双向关系边
    │
    ├─ 向后兼容写入 claw_pi_agent_insights（team_knowledge）
    │
    └─ 标记 claw_task_log.librarian_processed = 1

结果：知识图谱持续增长，下次用户提问时可自动召回
```

### 流程二：知识检索注入（每次 Worker 处理任务）

```
Worker 接收任务 payload
    │
    ├─ 加载技能 SKILL.md
    ├─ 加载团队快照（data_bridge/team_context，Redis 缓存 30min）
    │
    ├─ KnowledgeRetriever.retrieve_for_query(input_text)
    │   └─ [详见上方四步管线]
    │   └─ 返回 Markdown 知识上下文（≤4000 字符）
    │
    ├─ 合并注入 system prompt：
    │   "## 知识库上下文（Knowledge Context）
    │    ### 人物知识
    │    **张三 · RAG 研究进展** 🧠
    │    张三目前重点研究..."
    │
    └─ 调用 LLM（附带知识上下文，回答更准确）
```

### 流程三：实时进度推送（队列感知 UX）

```
用户提交任务（POST /api/chat）
    │
    Gateway 计算：
    │   queue_position = Redis LLEN task_queue
    │   avg_ms = Redis GET stats:avg_duration_ms（默认 25000）
    │   parallelism = min(活跃 Worker 数, CONCURRENT_CONSUMERS=5)
    │   estimated_wait_seconds = max(3, (queue_position / parallelism) * avg_ms / 1000)
    │
    同步返回：
    │   { task_id, queue_position: 2, estimated_wait_seconds: 35 }
    │
    WebSocket 推送 task_queued：
    │   前端显示 "排队中，第 2 位，约 35 秒"  + ETA 倒计时
    │
    Worker 处理中，每完成一个阶段发布进度：
    │   Redis PUBLISH task:progress {step:"llm_thinking", percent:25}
    │   Gateway Pub/Sub 转发 → WebSocket task_progress
    │   前端显示动态进度条 + 步骤描述
    │
    Worker 完成后：
    │   POST /gateway/callback → task_update WebSocket
    │   Worker 更新 stats:avg_duration_ms（滚动 200 条均值）
    │
    前端切换状态：queued → processing → completed
```

### 流程四：会话工作记忆管理

```
用户首次提问（session_key = "feishu:xxx"）
    │
    MemoryManager.get_session：
    │   1. 查 Redis openclaw_teamlab:prod:memory:feishu:xxx → miss
    │   2. 查 MySQL claw_memory_sessions WHERE session_key = 'feishu:xxx' → 新建空 session
    │   3. 回填 Redis（TTL 3600s）
    │
    Worker 处理完成后，可调用：
    │   mm.add_fact("feishu:xxx", "用户询问了张三的风险评估")
    │   mm.set_active_entities("feishu:xxx", ["张三"])
    │
用户第二次提问（同一会话）
    │
    MemoryManager.format_working_memory() 返回：
    │   "## 会话工作记忆
    │    **当前活跃实体**: 张三
    │    **工作记忆（本次对话）**:
    │    - 用户询问了张三的风险评估"
    │
    └─ 注入 system prompt → LLM 拥有上下文连续性
```

---

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/knowledge/search?q=...&k=10` | 语义 + 关键词混合检索 |
| GET | `/api/knowledge/entity/{entity_id}` | 实体完整知识画像 |
| GET | `/api/knowledge/graph/{entity_id}` | 实体子图（节点 + 边，可视化用） |
| GET | `/api/knowledge/stats` | 知识库统计（节点数、向量覆盖率等） |
| POST | `/api/knowledge/nodes` | 手动写入知识节点（自动生成嵌入） |
| DELETE | `/api/knowledge/nodes/{id}` | 删除节点及关联边 |
| GET | `/api/knowledge/memory/{session_key}` | 获取会话工作记忆 |
| POST | `/api/knowledge/memory/{session_key}/facts` | 向工作记忆添加事实 |
| DELETE | `/api/knowledge/memory/{session_key}` | 清除工作记忆 |

---

## 前端 UI（知识图谱 Tab）

Web UI (`http://127.0.0.1:10301`) 的"🧠 知识图谱"标签页包含：

| 子面板 | 功能 |
|--------|------|
| 🔍 检索 | 输入查询文本，显示语义检索结果（含相关度分数、重要性） |
| 👤 画像 | 按实体名查看完整知识画像 + 关系图（from → relation → to） |
| ➕ 写入 | 手动写入知识节点（选类型、填内容、设重要性） |
| 📊 统计 | 向量覆盖率进度条、节点类型分布、边数、摘要数 |

---

## 扩展方向

| 方向 | 说明 |
|------|------|
| 专用向量数据库 | 数据量超 10 万节点时迁移到 Qdrant / pgvector，保持 API 接口不变 |
| NER 增强实体提取 | 替换 retriever.py 中的正则提取，引入轻量 NER 模型（spaCy） |
| 知识图谱可视化 | 前端引入 force-graph.js，将 `/api/knowledge/graph` 数据渲染为交互式图 |
| 记忆衰减机制 | Evolver 定期降低低访问节点的 importance，自动归档到 L4 |
| 团队 Wiki 接入 | 将飞书文档/Notion 页面导入 claw_knowledge_nodes，扩展知识来源 |
