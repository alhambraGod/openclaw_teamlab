# Database Schema

OpenClaw TeamLab 使用 MySQL 8.0，数据库名 `openclaw_teamlab`，全部表使用 `utf8mb4` 字符集。另有对 CoEvo 生产库 `cognalign_coevo_prod` 的只读访问（通过独立连接池）。

---

## 数据库架构分层

```
┌─────────────────────────────────────────────────────────────┐
│           openclaw_teamlab  (主库，读写)                      │
│                                                             │
│  ── 业务数据 ──────────────────────────────────────────────  │
│  claw_students               claw_capability_dimensions               │
│  claw_capability_scores      claw_progress_events                     │
│  claw_meetings               claw_research_directions                 │
│  claw_collaboration_recommendations   claw_research_trends            │
│  claw_email_digests          claw_pi_config   claw_conversations           │
│                                                             │
│  ── 任务与洞见 ─────────────────────────────────────────────  │
│  claw_task_log               claw_pi_agent_insights                   │
│  claw_meeting_insights       claw_student_risk_scores                 │
│  claw_student_narratives     claw_action_item_tracker                 │
│  claw_research_direction_clusters   claw_research_direction_ideas     │
│                                                             │
│  ── 知识图谱（分层记忆，Migration 003）───────────────────────  │
│  claw_knowledge_nodes        claw_knowledge_edges                     │
│  claw_memory_summaries       claw_memory_sessions                     │
│  claw_knowledge_access_log                                       │
│                                                             │
│  ── CoEvo 集成 ─────────────────────────────────────────────  │
│  claw_coevo_student_links    claw_coevo_sync_logs                     │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│           cognalign_coevo_prod  (只读数据源)                  │
│                                                             │
│  users  projects  project_members  claw_meetings                 │
│  meeting_attendees  meeting_reports                         │
│  claw_collaboration_recommendations  research_plans              │
│  agent_memories                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 实体关系图（核心表）

```
┌──────────────┐       ┌─────────────────────┐       ┌──────────────────────┐
│   claw_students   │──1:N──│  claw_capability_scores   │──N:1──│ claw_capability_dimensions│
│  id (PK)     │       │  student_id (FK)     │       │  id (PK)             │
│  name        │       │  dimension_id (FK)   │       │  name (unique)       │
│  feishu_id   │       │  score DECIMAL(3,1)  │       │  label               │
│  degree_type │       │  assessed_at         │       │  category            │
│  status      │       └─────────────────────┘       └──────────────────────┘
│              │──1:N──┌─────────────────────┐
│              │       │  claw_progress_events    │
│              │       │  event_type         │
│              │       │  event_date         │
│              │       └─────────────────────┘
│              │──N:M──┌──────────────────────────────┐
└──────────────┘       │ claw_collaboration_recommendations │
                       │  student_a_id  student_b_id  │
                       │  complementarity_score        │
                       └──────────────────────────────┘

┌──────────────┐       ┌──────────────────┐──self──┐
│  claw_task_log    │       │ research_dirs    │        │ (parent_id FK, 树形)
│  task_id UUID│       │  source          │◄───────┘
│  skill_used  │       │  status          │
│  status      │       │  priority        │
│  duration_ms │       └──────────────────┘
└──────────────┘

── 知识图谱关系 ──────────────────────────────────────────────────

┌──────────────────┐    N:M via    ┌──────────────────┐
│  claw_knowledge_nodes │──knowledge_──→│  claw_knowledge_nodes │
│  entity_type     │    edges      │  (邻居节点)       │
│  entity_id       │               └──────────────────┘
│  content         │
│  embedding (BLOB)│──1:N──┌──────────────────────┐
│  importance 0-100│       │  claw_knowledge_access_log │
└──────────────────┘       │  query_text  score   │
                           └──────────────────────┘

┌──────────────────┐       ┌──────────────────┐
│  claw_memory_sessions │       │ claw_memory_summaries │
│  session_key UNIQ│       │ entity_type/id   │
│  working_facts   │       │ period(day/wk/mo)│
│  pinned_facts    │       │ summary_text     │
│  active_entities │       │ embedding (BLOB) │
└──────────────────┘       └──────────────────┘
```

---

## 表结构详情

### claw_students

核心学生档案表。

| 列 | 类型 | 说明 |
|----|------|------|
| id | INT PK AUTO | 主键 |
| name | VARCHAR(100) NOT NULL | 姓名 |
| email | VARCHAR(200) | 邮箱 |
| feishu_open_id | VARCHAR(100) INDEX | 飞书用户 ID |
| research_area | TEXT | 研究方向 |
| degree_type | ENUM('phd','master','postdoc','undergrad') | 学位类型 |
| status | ENUM('active','graduated','on_leave') INDEX | 当前状态 |
| tags | JSON | 灵活标签（如 `["ml","nlp"]`） |
| advisor_notes | TEXT | PI 私密备注 |

### claw_capability_scores

学生能力评估时序记录（雷达图数据源）。

| 列 | 类型 | 说明 |
|----|------|------|
| student_id | INT FK→claw_students | 学生 |
| dimension_id | INT FK→claw_capability_dimensions | 能力维度 |
| score | DECIMAL(3,1) | 评分 0.0-10.0 |
| assessed_at | DATE | 评估日期 |
| evidence | TEXT | 评分依据 |

**索引**：`idx_student_time(student_id, assessed_at)`

**默认维度**（8 个）：`literature`、`coding`、`writing`、`experiment`、`presentation`、`collaboration`、`innovation`、`self_management`

### claw_task_log

所有任务执行的审计日志（含队列感知字段）。

| 列 | 类型 | 说明 |
|----|------|------|
| task_id | VARCHAR(64) UNIQUE | UUID 任务标识 |
| user_id | VARCHAR(200) INDEX | 提交者 |
| source | ENUM('feishu','web','api','scheduler') | 来源 |
| skill_used | VARCHAR(100) INDEX | 执行的技能 |
| input_text | TEXT | 原始用户输入 |
| result_summary | TEXT | 简短结果 |
| result_data | JSON | 完整结果数据 |
| status | ENUM('queued','running','completed','failed') | 执行状态 |
| duration_ms | INT | 执行耗时 |
| worker_id | VARCHAR(50) | 执行的 Worker |
| librarian_processed | TINYINT | Librarian 是否已提取知识 |

### claw_pi_agent_insights

AI 生成的洞见持久化（全球研究热点、跨项目协作、团队知识等）。

| 列 | 类型 | 说明 |
|----|------|------|
| id | BIGINT UNSIGNED PK | 主键 |
| insight_type | VARCHAR(64) | `global_research`\|`cross_project`\|`team_knowledge`\|`system_evolution`\|`evolution_suggestion` |
| subject | VARCHAR(255) | 洞见主题/标题 |
| content | MEDIUMTEXT | 洞见正文 |
| metadata | JSON | 额外结构化数据（paper_ids、scan_date 等） |
| created_at | DATETIME | 创建时间 |

---

## 知识图谱表（Migration 003）

> 实现分层记忆架构（L2–L5），支持语义检索与知识图谱遍历。

### claw_knowledge_nodes（L2 语义记忆）

每条记录是一个知识原子（Knowledge Atom），可附带向量嵌入。

| 列 | 类型 | 说明 |
|----|------|------|
| id | BIGINT UNSIGNED PK | 主键 |
| entity_type | ENUM('person','project','concept','research','insight','event') | 实体类型 |
| entity_id | VARCHAR(255) INDEX | 实体标识（人名/项目名等） |
| title | VARCHAR(512) | 知识点标题 |
| content | MEDIUMTEXT | 完整知识文本 |
| source | ENUM('librarian','user','scheduler','evolver','manual','coevo') | 写入来源 |
| importance | TINYINT UNSIGNED DEFAULT 50 | 重要性 0-100，访问时自动提升 (+2/次，上限 100) |
| confidence | DECIMAL(3,2) DEFAULT 0.80 | 置信度 |
| access_count | INT UNSIGNED DEFAULT 0 | 访问次数（用于重要性自适应） |
| last_accessed_at | DATETIME | 最近访问时间 |
| embedding | MEDIUMBLOB | float32[1536] 小端二进制（6144 字节），来自 text-embedding-3-small |
| metadata | JSON | 额外结构化数据 |
| expires_at | DATETIME | NULL = 永久，有值则自动过期 |

**索引**：`idx_entity(entity_type, entity_id)`、`idx_importance(importance DESC, last_accessed_at DESC)`

**存储设计**：向量以 `struct.pack('<1536f', ...)` 存入 MEDIUMBLOB，Python-side cosine 相似度计算，不依赖向量数据库扩展。

### claw_knowledge_edges（L3 结构记忆 / 知识图谱）

建模实体间有向关系，支持图遍历。

| 列 | 类型 | 说明 |
|----|------|------|
| id | BIGINT UNSIGNED PK | 主键 |
| from_node_id | BIGINT UNSIGNED INDEX | 源节点 |
| to_node_id | BIGINT UNSIGNED INDEX | 目标节点 |
| relation | VARCHAR(64) INDEX | 关系类型（见下表） |
| weight | FLOAT DEFAULT 1.0 | 关系强度 |
| bidirectional | TINYINT(1) DEFAULT 0 | 是否双向 |
| evidence | TEXT | 支撑此关系的证据文本 |

**UNIQUE KEY**：`uniq_edge(from_node_id, to_node_id, relation)`

**预定义关系类型**：

| 关系类型 | 语义 |
|---------|------|
| `collaborates_with` | 合作关系 |
| `works_on` | 参与项目 |
| `mentors` | 指导关系 |
| `interested_in` | 研究兴趣 |
| `related_to` | 概念关联 |
| `cites` | 引用关系 |
| `published_with` | 联合发表 |
| `co_mentioned` | 在同一对话中共同出现（Librarian 自动建立） |
| `evolves_from` | 研究方向演化 |

### claw_memory_summaries（L4 档案记忆）

实体知识的周期压缩摘要，支持长期记忆回溯。

| 列 | 类型 | 说明 |
|----|------|------|
| entity_type | ENUM('person','project','global') | 实体类型 |
| entity_id | VARCHAR(255) | 实体标识 |
| period | ENUM('daily','weekly','monthly') | 压缩周期 |
| period_start / period_end | DATE | 时间段 |
| summary_text | MEDIUMTEXT | 压缩摘要 |
| key_events | JSON | 提取的关键事件列表 |
| key_facts | JSON | 提取的关键事实列表 |
| embedding | MEDIUMBLOB | 摘要向量（支持跨时段语义检索） |

**UNIQUE KEY**：`uniq_period(entity_type, entity_id, period, period_start)`

### claw_memory_sessions（L5 工作记忆）

per-user 对话状态持久化，跨会话记忆延续（MemGPT 风格）。

| 列 | 类型 | 说明 |
|----|------|------|
| session_key | VARCHAR(256) UNIQUE | 会话标识，如 `"web:pi"` 或 `"feishu:xxx"` |
| user_id | VARCHAR(128) INDEX | 用户 ID |
| working_facts | JSON | 当前对话激活的事实列表（上限 20 条，溢出压缩到 claw_knowledge_nodes） |
| active_entities | JSON | 最近提到的实体列表（上限 10 个） |
| pinned_facts | JSON | 用户明确要求固定的永久记忆 |
| persona_notes | TEXT | PI 对此用户的个性化备注 |
| turn_count | INT UNSIGNED | 累计对话轮次 |
| last_active_at | DATETIME | 最后活跃时间 |

**Redis 同步**：活跃会话同时缓存在 Redis（TTL 3600s），读优先 Redis，写后同步 MySQL。

### claw_knowledge_access_log

知识访问记录，用于重要性自适应调整和 Evolver 分析。

| 列 | 类型 | 说明 |
|----|------|------|
| node_id | BIGINT UNSIGNED INDEX | 被访问的节点 |
| session_key | VARCHAR(256) | 触发访问的会话 |
| query_text | VARCHAR(512) | 触发检索的查询文本 |
| score | FLOAT | 检索相关度分数 |
| accessed_at | DATETIME | 访问时间 |

---

## 混合检索策略

```
输入：query 文本 + session_entities（从工作记忆提取）
    │
    ├─ Step 1: SQL 预筛（关键词 LIKE 过滤，最多 200 个候选节点）
    │           按 importance DESC, last_accessed_at DESC 排序
    │
    ├─ Step 2: 向量重排（EmbeddingService.cosine_similarity）
    │           综合分 = cosine × (0.5 + importance/200)
    │           向量不可用时退化为关键词排序
    │
    ├─ Step 3: 图谱扩展（Top-3 节点的 1-hop 邻居，补充关联实体）
    │
    ├─ Step 4: 档案补充（L4 claw_memory_summaries，知识不足时兜底）
    │
    └─ Step 5: 格式化 Markdown（≤4000 字符，按类型分组输出）
               注入 Worker 的 LLM system prompt
```

---

## Migration 文件

| 文件 | 内容 |
|------|------|
| `data/migrations/001_init.sql` | 完整初始 Schema（业务表 + 任务表） |
| `data/migrations/002_pi_agent_insights.sql` | AI 洞见持久化表 |
| `data/migrations/003_knowledge_graph.sql` | 知识图谱分层记忆表（claw_knowledge_nodes 等 5 张表） |
| `data/seeds/001_defaults.sql` | 默认能力维度 + PI 配置初始数据 |
