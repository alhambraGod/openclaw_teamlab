-- ============================================================
-- OpenClaw TeamLab — Knowledge Graph & Layered Memory Schema
-- Migration 003 | 分层知识存储架构
--
-- 架构分层（仿 MemGPT + A-MEM + Zep 最佳实践）：
--
--   L0 Working Memory  → Redis (per-session, TTL, 已实现)
--   L1 Episodic Memory → claw_task_log + claw_conversations (已有)
--   L2 Semantic Memory → claw_knowledge_nodes  (本次新建，含向量)
--   L3 Structural Mem  → claw_knowledge_edges  (本次新建，知识图谱)
--   L4 Archival Memory → claw_memory_summaries (本次新建，周期压缩)
--   L5 Session State   → claw_memory_sessions  (本次新建，工作记忆持久化)
-- ============================================================

USE openclaw_teamlab;

-- ── L2: 语义记忆 — 知识节点 ─────────────────────────────────
-- 每条记录是一个知识原子（Knowledge Atom）
-- 可属于：人物 / 项目 / 概念 / 研究方向 / 洞见 / 事件
CREATE TABLE IF NOT EXISTS claw_knowledge_nodes (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,

    -- 实体标识
    entity_type     ENUM(
                        'person',    -- 团队成员（学生/导师）
                        'project',   -- 研究项目
                        'concept',   -- 研究概念/关键词
                        'research',  -- 研究方向/主题
                        'insight',   -- AI 生成洞见
                        'event'      -- 事件（论文投稿/里程碑等）
                    ) NOT NULL,
    entity_id       VARCHAR(255) NOT NULL COMMENT '实体唯一标识，person用name，project用project_name',

    -- 内容
    title           VARCHAR(512) NOT NULL  COMMENT '知识点标题（短摘要）',
    content         MEDIUMTEXT NOT NULL    COMMENT '完整知识文本',

    -- 元数据
    source          ENUM('librarian','user','scheduler','evolver','manual','coevo') DEFAULT 'librarian',
    importance      TINYINT UNSIGNED DEFAULT 50 COMMENT '重要性 0-100，越高越优先召回',
    confidence      DECIMAL(3,2) DEFAULT 0.80   COMMENT '置信度 0.00-1.00',

    -- 访问统计（用于重要性自动调整）
    access_count    INT UNSIGNED DEFAULT 0,
    last_accessed_at DATETIME,

    -- 向量嵌入（text-embedding-3-small, 1536 dims, float32 binary packed = 6144 bytes）
    -- NULL 表示尚未生成嵌入（退化为关键词检索）
    embedding       MEDIUMBLOB COMMENT 'float32[1536] little-endian binary pack',

    -- 额外结构化数据
    metadata        JSON COMMENT '如 paper_ids, meeting_ids, project_ids, tags 等',

    -- 生命周期
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME ON UPDATE CURRENT_TIMESTAMP,
    expires_at      DATETIME COMMENT 'NULL=永久，有值则自动过期',

    INDEX idx_entity            (entity_type, entity_id),
    INDEX idx_importance        (importance DESC, last_accessed_at DESC),
    INDEX idx_source_created    (source, created_at DESC),
    INDEX idx_entity_id         (entity_id),
    INDEX idx_expires           (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='L2 语义记忆：团队/个人知识原子，含向量嵌入支持语义检索';


-- ── L3: 结构记忆 — 知识图谱边 ───────────────────────────────
-- 建模实体间的关系（有向图，支持双向标记）
CREATE TABLE IF NOT EXISTS claw_knowledge_edges (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,

    from_node_id    BIGINT UNSIGNED NOT NULL COMMENT '源知识节点',
    to_node_id      BIGINT UNSIGNED NOT NULL COMMENT '目标知识节点',

    -- 关系语义（预定义 + 可扩展）
    relation        VARCHAR(64) NOT NULL COMMENT
                    '关系类型: collaborates_with | works_on | mentors | knows |
                     interested_in | related_to | cites | published_with |
                     blocks | supports | contradicts | evolves_from',

    weight          FLOAT DEFAULT 1.0  COMMENT '关系强度 (0,∞)，越高越强',
    bidirectional   TINYINT(1) DEFAULT 0 COMMENT '1=双向关系',

    -- 关系证据
    evidence        TEXT COMMENT '支撑此关系的证据文本',
    metadata        JSON,

    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME ON UPDATE CURRENT_TIMESTAMP,

    -- 防止重复关系
    UNIQUE KEY uniq_edge (from_node_id, to_node_id, relation),
    INDEX idx_from     (from_node_id),
    INDEX idx_to       (to_node_id),
    INDEX idx_relation (relation),
    INDEX idx_weight   (weight DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='L3 结构记忆：知识图谱有向边，建模实体间语义关系';


-- ── L4: 档案记忆 — 周期压缩摘要 ─────────────────────────────
-- 将某实体在某时间段的所有知识压缩为单一摘要（类似 MemGPT archival memory）
CREATE TABLE IF NOT EXISTS claw_memory_summaries (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,

    entity_type     ENUM('person','project','global') NOT NULL,
    entity_id       VARCHAR(255) NOT NULL,

    -- 时间粒度
    period          ENUM('daily','weekly','monthly') NOT NULL,
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,

    -- 压缩摘要
    summary_text    MEDIUMTEXT NOT NULL,
    key_events      JSON COMMENT '提取的关键事件列表',
    key_facts       JSON COMMENT '提取的关键事实列表',

    -- 向量嵌入（支持跨时间段语义检索）
    embedding       MEDIUMBLOB,

    metadata        JSON,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE KEY uniq_period (entity_type, entity_id, period, period_start),
    INDEX idx_entity_period (entity_type, entity_id, period_start DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='L4 档案记忆：实体知识的周期压缩摘要，支持长期记忆回溯';


-- ── L5: 工作记忆会话 — per-user 持久化 ──────────────────────
-- 跨越 HTTP 无状态的用户工作记忆，类 MemGPT human/persona sections
CREATE TABLE IF NOT EXISTS claw_memory_sessions (
    id                  BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,

    -- 会话标识
    session_key         VARCHAR(256) NOT NULL UNIQUE COMMENT '"feishu:xxx" 或 "web:pi"',
    user_id             VARCHAR(128) NOT NULL,

    -- 工作记忆区（MemGPT inner context）
    working_facts       JSON NOT NULL DEFAULT (JSON_ARRAY())  COMMENT '当前对话激活的事实列表',
    active_entities     JSON DEFAULT (JSON_ARRAY())           COMMENT '最近提到的实体(人/项目)列表',
    pinned_facts        JSON DEFAULT (JSON_ARRAY())           COMMENT '用户明确要求 PIN 的永久记忆',
    persona_notes       TEXT                                  COMMENT 'PI 对此用户的个性化备注',

    -- 对话统计
    turn_count          INT UNSIGNED DEFAULT 0,
    total_tokens_used   BIGINT UNSIGNED DEFAULT 0,

    -- 生命周期
    last_active_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_user          (user_id),
    INDEX idx_last_active   (last_active_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='L5 工作记忆：per-user 对话状态持久化，支持跨会话记忆延续';


-- ── 知识访问日志（可选，用于分析什么知识被频繁使用）──────────────
CREATE TABLE IF NOT EXISTS claw_knowledge_access_log (
    id          BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    node_id     BIGINT UNSIGNED NOT NULL,
    session_key VARCHAR(256),
    query_text  VARCHAR(512) COMMENT '触发此次检索的查询文本',
    score       FLOAT COMMENT '检索相关度分数',
    accessed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_node    (node_id),
    INDEX idx_session (session_key),
    INDEX idx_time    (accessed_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='知识访问记录，用于重要性自适应调整和系统进化分析';
