# Architecture Overview

OpenClaw TeamLab 是一个 AI 驱动的研究团队管理平台，基于 [OpenClaw](https://github.com/anthropics/openclaw) 框架构建。帮助 PI（导师）通过智能自动化管理大型研究团队。

---

## 系统组件总览

```
                         ┌──────────────────────┐
                         │   Feishu / Web Browser│  用户通过飞书机器人
                         │   (OpenClaw :10300)   │  或 Web 仪表板交互
                         └──────────┬───────────┘
                                    │  HTTP / WebSocket
                                    ▼
┌───────────────────────────────────────────────────────────────┐
│                    Gateway  (port 10301)                       │
│                                                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐  ┌──────────┐  │
│  │ REST API │  │WebSocket │  │  Agent API   │  │ Static   │  │
│  │ /api/*   │  │  /ws     │  │ /api/agent/* │  │  Files   │  │
│  └────┬─────┘  └────┬─────┘  └──────┬───────┘  └──────────┘  │
│       │              │              │                          │
│       └──────────────┴──────────────┘                         │
│                      │                                        │
│     ┌─────────────────┴──────────────────┐                    │
│     │        Intent Router               │  关键词匹配 +       │
│     │   (config/agents.yaml)             │  LLM 兜底分类       │
│     └─────────────────┬──────────────────┘                    │
│                       │                                       │
│  ┌────────────────────┴──────────────────────────────────┐    │
│  │    5 × Concurrent Queue Consumers (Redis BRPOP)       │    │
│  │    原子消费，无重复分发，支持 50 并发用户               │    │
│  └────────────────────┬──────────────────────────────────┘    │
│                       │  Redis Pub/Sub (task:progress)         │
│                       │  ←── Worker 实时进度广播 ──→ WebSocket  │
└───────────────────────┼───────────────────────────────────────┘
                        │ Redis Task Queue (LPUSH / BRPOP)
                        ▼
┌───────────────────────────────────────────────────────────────┐
│              Worker Pool  (ports 10310-10329)                  │
│                                                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐       ┌──────────┐ │
│  │ Worker-0 │  │ Worker-1 │  │ Worker-2 │  ...  │  W-19   │ │
│  │  :10310  │  │  :10311  │  │  :10312  │       │  :10329 │ │
│  └──────────┘  └──────────┘  └──────────┘       └──────────┘ │
│                                                               │
│  每个 Worker 是无状态 FastAPI 微服务，按需加载任意 SKILL.md    │
│  任务处理阶段通过 Redis Pub/Sub 发布进度事件                   │
└──────────────────────┬────────────────────────────────────────┘
                       │
          ┌────────────┼──────────────┐
          ▼            ▼              ▼
   ┌───────────┐ ┌──────────┐ ┌─────────────────────┐
   │   MySQL   │ │  Redis   │ │  LLM API             │
   │ 2 个库    │ │ 缓存/队列 │ │  (OpenAI-compatible) │
   │ (主库+只读)│ │ Pub/Sub  │ │  text-embedding-3-   │
   └─────┬─────┘ └──────────┘ │  small (向量嵌入)     │
         │                    └─────────────────────┘
         │
┌────────┴──────────────────────────────────────────────────────┐
│              Knowledge Layer  (知识分层架构)                    │
│                                                               │
│  L2 claw_knowledge_nodes  ← Librarian/CoevoSync 写入，向量嵌入     │
│  L3 claw_knowledge_edges  ← 实体关系图谱（协作/研究/师徒等）        │
│  L4 claw_memory_summaries ← 周期压缩蒸馏（日/周/月摘要）           │
│  L5 claw_memory_sessions  ← per-user 工作记忆，跨会话延续          │
│                                                               │
│  Worker 调用 KnowledgeRetriever 在处理前注入相关知识上下文     │
└───────────────────────────────────────────────────────────────┘
           ▲                           ▲
           │                           │ 增量同步（每4小时）
           │                           │ watermark 驱动，只拉新数据
┌──────────┴────────────────────────────────────────────────────┐
│         CoEvo Knowledge Sync  (data_bridge/coevo_knowledge_sync)│
│                                                               │
│  数据源（只读）：cognalign_coevo_prod                           │
│                                                               │
│  ① 会议后报告  → person 节点（导师点评 / 学生总结）              │
│  ② 研究规划    → research 节点（目标 / 周期节点）                │
│  ③ 协作推荐    → person 节点 + recommended_collaborator 边     │
│  ④ Agent 记忆  → person/project 节点（CAMA 已提炼的高质量记忆） │
│                                                               │
│  Redis watermark 记录每类数据上次同步时间，保证幂等增量          │
│  手动触发：POST /api/system/coevo/sync                         │
└───────────────────────────────────────────────────────────────┘
                       ▲
                       │
┌──────────────────────┴────────────────────────────────────────┐
│              Scheduler  (port 10302)                           │
│                                                               │
│  APScheduler cron 作业，向同一 Redis 队列提交任务：            │
│                                                               │
│  */4h   coevo_sync (CoEvo数据增量同步)                         │
│  06:00  global_research_scan   │  01:00  librarian (知识提取) │
│  07:00  email_digest           │  04:00  evolver  (进化分析)  │
│  Mon 03:00  cross_project_analyze                             │
│                                                               │
│  支持通过 UI (10301) 动态修改 cron 频率，持久化到 agents.yaml  │
└───────────────────────────────────────────────────────────────┘

                 自主角色 (Autonomous Roles)
┌─────────────────────────────────────────────────────────────┐
│  Librarian（每日 01:00）          │  Evolver（每周一 04:00）  │
│  ─────────────────────────────  │  ───────────────────────  │
│  阶段1：扫描 claw_task_log       │  采集 openclaw 系统性能    │
│  LLM 提取团队知识原子             │  +                        │
│  阶段2：调用 CoevoKnowledgeSync  │  从 coevo 采集团队活跃度   │
│  吸收 coevo 最新数据              │  （会议率/报告提交率等）   │
│  写入 claw_knowledge_nodes/edges │  LLM 综合分析两维度数据    │
│                                 │  生成进化建议+双维度报告    │
└─────────────────────────────────────────────────────────────┘
```

---

## 端口分配

| 组件 | 端口 | 说明 |
|------|------|------|
| OpenClaw 前端 | 10300 | 用户对话入口（Docker） |
| Gateway | 10301 | HTTP API + Web UI + Agent API |
| Scheduler | 10302 | APScheduler cron 引擎 |
| Workers | 10310–10329 | 最多 20 个通用 Worker |

---

## 数据来源与时效性

### 双库架构

| 数据库 | 角色 | 访问权限 | 说明 |
|--------|------|----------|------|
| `cognalign_coevo_prod` | **事实真相（Source of Truth）** | 只读 | 团队实际活动数据：会议、报告、研究规划、协作推荐、Agent 记忆 |
| `openclaw_teamlab` | **分析智能层** | 读写 | 知识图谱、任务日志、能力评分、进化洞见、会话状态 |

### CoEvo 数据流与时效性保障

```
cognalign_coevo_prod（只读）
    │
    │  增量同步（CoevoKnowledgeSync）
    │  ├── 触发方式1: 调度器每4小时自动执行（__coevo_sync__）
    │  ├── 触发方式2: POST /api/system/coevo/sync 手动触发（如刚开完会）
    │  └── 触发方式3: Librarian 每日01:00运行时内嵌调用
    │
    │  watermark 机制（Redis key: coevo_wm:<type>）
    │  ├── 记录每类数据上次处理的 updated_at 时间戳
    │  ├── 每次只拉取水印之后的新增/变更记录（增量，非全量）
    │  └── 首次运行默认回溯30天历史数据
    ▼
claw_knowledge_nodes / claw_knowledge_edges（本地知识图谱）
    │
    ├── 会议后报告  → person 节点（重要性70）含导师点评/学生总结
    ├── 研究规划   → research 节点（重要性75）含研究目标/周期计划
    ├── 协作推荐   → person 节点（重要性72）+ recommended_collaborator 边
    └── Agent 记忆 → person/project 节点（重要性65-80，按类型）
    │
    ▼
KnowledgeRetriever（工作处理前注入上下文）
    │
    └── Worker 处理用户查询时，自动检索相关知识注入 LLM prompt
        → 系统"懂团队"的能力来自这里
```

### 数据一致性策略

- **只读原则**：`cognalign_coevo_prod` 任何时候绝不写入，TeamLab 是纯消费者
- **增量幂等**：`upsert_node` 使用 `(entity_type, entity_id, title)` 唯一约束，重复同步无副作用
- **时效等级**：
  - 用户实时查询：直接查 coevo（`data_bridge/queries.py`），延迟 < 100ms
  - 知识图谱积累：每4小时同步一次，普通场景可接受
  - 重要事件后：调用 `POST /api/system/coevo/sync` 立即同步

---

## 核心设计决策

### 通用 Worker 池（非专用 Agent）

Worker 是**通用**的——任何 Worker 可执行任意技能，按需从 `SKILL.md` 文件加载。设计优势：

- 资源利用率最大化（无空闲专用 Agent）
- 扩展简单（增加 Worker 实例即可）
- 通过共享任务队列支持 50+ 并发用户

### 并行队列消费与 ETA 预估

Gateway 启动 **5 个并发队列消费者**（`CONCURRENT_CONSUMERS = 5`），各自独立 `BRPOP`：

- Redis 原子 POP 保证同一任务不被重复消费
- 提交时根据队列长度、活跃 Worker 数、历史平均耗时（`stats:avg_duration_ms`）计算 ETA
- WebSocket 实时推送排队位置更新、进度事件（`task_queued` / `task_progress` / `task_update`）

### 两阶段意图分类

1. **快速关键词匹配**：从 `config/agents.yaml` 规则（亚毫秒级）
2. **LLM 兜底**：关键词无命中或置信度低时调用 LLM 分类

### 分层知识存储（MemGPT 启发）

| 层级 | 存储位置 | 生命周期 | 用途 |
|------|---------|---------|------|
| L0 Working Memory | Redis | TTL，任务内 | 飞行中上下文 |
| L1 Episodic Memory | claw_task_log + claw_conversations | 永久 | 时序历史 |
| L2 Semantic Memory | claw_knowledge_nodes | 永久，含向量 | 语义检索 |
| L3 Structural Memory | claw_knowledge_edges | 永久 | 知识图谱关系 |
| L4 Archival Memory | claw_memory_summaries | 永久 | 周期压缩摘要 |
| L5 Session State | claw_memory_sessions | 永久 | 跨会话工作记忆 |

### 自主角色驱动的系统进化

调度器定期触发两个自主角色，使系统持续学习和改进：

- **Librarian**：从已完成对话中提取团队知识，写入知识图谱
- **Evolver**：分析系统性能指标，生成改进建议和健康报告

---

## 数据流

### 用户请求流程（含队列 UX）

```
1. 用户发送消息（飞书 / Web /api/chat）
2. Gateway 分类意图 → 映射到技能
3. Gateway LPUSH 任务到 Redis 队列
4. 同步返回：task_id + queue_position + estimated_wait_seconds
5. WebSocket 推送 task_queued 事件（前端显示排队位置 + ETA 倒计时）

── 并行消费阶段 ─────────────────────────────────────────────────
6. 5 个消费者之一 BRPOP 取出任务，找到空闲 Worker 分发
7. Worker 加载技能，注入团队上下文 + 知识检索结果
8. Worker 每完成一个阶段 → Redis Pub/Sub 发布进度事件
9. Gateway Pub/Sub 监听器转发进度到 WebSocket（前端显示步骤+进度条）

── 完成阶段 ─────────────────────────────────────────────────────
10. Worker 完成任务 → POST 结果到 Gateway callback
11. Gateway 更新 claw_task_log，通过 WebSocket 推送 task_update（status=completed）
12. Worker 更新 stats:avg_duration_ms（滚动均值，用于后续 ETA 计算）
13. 飞书场景：callback_url 触发主动回复用户
```

### 知识检索注入流程

```
Worker 收到任务
    │
    ├─ 1. 加载技能 (SKILL.md + scripts)
    │
    ├─ 2. 加载团队快照 (data_bridge/team_context → Redis 缓存 30min)
    │
    ├─ 3. KnowledgeRetriever.retrieve_for_query(input_text)
    │       │
    │       ├─ 实体提取：从 query 识别人名、项目名
    │       │
    │       ├─ L2 语义检索：SQL 关键词预筛 → Python-side cosine rerank
    │       │   (text-embedding-3-small, 1536 dims, float32 binary in BLOB)
    │       │
    │       ├─ L3 图谱扩展：Top-3 节点的 1-hop 邻居（补充关联实体）
    │       │
    │       └─ L4 档案补充：实体历史摘要（知识不足时兜底）
    │
    ├─ 4. 合并注入 system prompt（≤4000 字符，超出截断）
    │
    └─ 5. 调用 LLM 生成回答
```

### 知识积累流程（Librarian）

```
Librarian 定时触发 (每日 01:00)
    │
    ├─ 1. 扫描 claw_task_log：status=completed, source≠scheduler, 未处理
    │
    ├─ 2. 批量提取（每批 50 条）：
    │       LLM 分析问答对 → JSON 数组 [{subject, fact, type}]
    │
    ├─ 3. 写入 claw_knowledge_nodes (L2)：
    │       entity_type = person/project/research/insight
    │       自动生成 text-embedding-3-small 向量嵌入
    │
    ├─ 4. 建立 claw_knowledge_edges (L3)：
    │       同一对话中出现的多个实体之间 → co_mentioned 关系边
    │
    ├─ 5. 向后兼容写入 claw_pi_agent_insights（team_knowledge 类型）
    │
    └─ 6. 标记 claw_task_log.librarian_processed=1，避免重复
```

### 自进化流程（Evolver）

```
Evolver 定时触发 (每周一 04:00)
    │
    ├─ 1. 收集 7 天系统统计：
    │       claw_task_log 总量 / 成功率 / 平均耗时 / Top 技能分布
    │
    ├─ 2. LLM 分析 → 生成进化建议：
    │       识别高失败率技能 / 发现可自动化的重复操作
    │
    ├─ 3. 写入 claw_pi_agent_insights：
    │       system_evolution（健康报告）
    │       evolution_suggestion（改进建议，含 priority）
    │
    └─ 4. 可通过 /api/agent/evolution-report 查阅
```

---

## 技术栈

| 层级 | 技术 |
|------|------|
| API 框架 | FastAPI (async) |
| 主数据库 | MySQL 8 (aiomysql + SQLAlchemy 2.0 async) |
| 只读数据源 | CoEvo MySQL (cognalign_coevo_prod，跨库只读) |
| 缓存 / 队列 | Redis (hiredis, BRPOP + Pub/Sub) |
| 向量嵌入 | OpenAI text-embedding-3-small (1536 dims, 通过已有代理) |
| LLM | OpenAI-compatible API (Gemini 等) |
| 飞书 SDK | lark-oapi (WebSocket 长连接模式，无需公网 IP) |
| 调度器 | APScheduler 3.x |
| 前端 | Vue.js 3 + TailwindCSS + ECharts (CDN，无构建步骤) |
| 进程管理 | teamlab 脚本 / Docker Compose |
