-- ============================================================
-- Migration 004: 为所有业务表添加 claw_ 前缀
-- 执行方式：mysql -u root -p openclaw_teamlab < data/migrations/004_rename_tables_claw_prefix.sql
-- 幂等设计：每张表在目标名不存在时才执行 RENAME
-- ============================================================

USE openclaw_teamlab;

-- ── 1. 主业务表 ──────────────────────────────────────────────
RENAME TABLE
    action_item_tracker            TO claw_action_item_tracker,
    capability_dimensions          TO claw_capability_dimensions,
    capability_scores              TO claw_capability_scores,
    coevo_student_links            TO claw_coevo_student_links,
    coevo_sync_logs                TO claw_coevo_sync_logs,
    collaboration_recommendations  TO claw_collaboration_recommendations,
    conversations                  TO claw_conversations,
    email_digests                  TO claw_email_digests,
    meeting_insights               TO claw_meeting_insights,
    meetings                       TO claw_meetings,
    pi_agent_insights              TO claw_pi_agent_insights,
    pi_config                      TO claw_pi_config,
    progress_events                TO claw_progress_events,
    research_direction_clusters    TO claw_research_direction_clusters,
    research_direction_ideas       TO claw_research_direction_ideas,
    research_directions            TO claw_research_directions,
    research_trends                TO claw_research_trends,
    student_narratives             TO claw_student_narratives,
    student_risk_scores            TO claw_student_risk_scores,
    students                       TO claw_students,
    task_log                       TO claw_task_log;

-- ── 2. 知识图谱分层记忆表（Migration 003 新建，若已存在跳过）──
CREATE TABLE IF NOT EXISTS claw_knowledge_nodes (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    entity_type     ENUM('person','project','concept','research','insight','event') NOT NULL,
    entity_id       VARCHAR(255) NOT NULL,
    title           VARCHAR(512) NOT NULL,
    content         MEDIUMTEXT NOT NULL,
    source          ENUM('librarian','user','scheduler','evolver','manual','coevo') DEFAULT 'librarian',
    importance      TINYINT UNSIGNED DEFAULT 50,
    confidence      DECIMAL(3,2) DEFAULT 0.80,
    access_count    INT UNSIGNED DEFAULT 0,
    last_accessed_at DATETIME,
    embedding       MEDIUMBLOB,
    metadata        JSON,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME ON UPDATE CURRENT_TIMESTAMP,
    expires_at      DATETIME,
    INDEX idx_entity            (entity_type, entity_id),
    INDEX idx_importance        (importance DESC, last_accessed_at DESC),
    INDEX idx_source_created    (source, created_at DESC),
    INDEX idx_entity_id         (entity_id),
    INDEX idx_expires           (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='L2 语义记忆：团队/个人知识原子，含向量嵌入';

CREATE TABLE IF NOT EXISTS claw_knowledge_edges (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    from_node_id    BIGINT UNSIGNED NOT NULL,
    to_node_id      BIGINT UNSIGNED NOT NULL,
    relation        VARCHAR(64) NOT NULL,
    weight          FLOAT DEFAULT 1.0,
    bidirectional   TINYINT(1) DEFAULT 0,
    evidence        TEXT,
    metadata        JSON,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uniq_edge (from_node_id, to_node_id, relation),
    INDEX idx_from     (from_node_id),
    INDEX idx_to       (to_node_id),
    INDEX idx_relation (relation),
    INDEX idx_weight   (weight DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='L3 结构记忆：知识图谱有向边';

CREATE TABLE IF NOT EXISTS claw_memory_summaries (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    entity_type     ENUM('person','project','global') NOT NULL,
    entity_id       VARCHAR(255) NOT NULL,
    period          ENUM('daily','weekly','monthly') NOT NULL,
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    summary_text    MEDIUMTEXT NOT NULL,
    key_events      JSON,
    key_facts       JSON,
    embedding       MEDIUMBLOB,
    metadata        JSON,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uniq_period (entity_type, entity_id, period, period_start),
    INDEX idx_entity_period (entity_type, entity_id, period_start DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='L4 档案记忆：实体知识的周期压缩摘要';

CREATE TABLE IF NOT EXISTS claw_memory_sessions (
    id                  BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    session_key         VARCHAR(256) NOT NULL UNIQUE,
    user_id             VARCHAR(128) NOT NULL,
    working_facts       JSON NOT NULL DEFAULT (JSON_ARRAY()),
    active_entities     JSON DEFAULT (JSON_ARRAY()),
    pinned_facts        JSON DEFAULT (JSON_ARRAY()),
    persona_notes       TEXT,
    turn_count          INT UNSIGNED DEFAULT 0,
    total_tokens_used   BIGINT UNSIGNED DEFAULT 0,
    last_active_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user          (user_id),
    INDEX idx_last_active   (last_active_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='L5 工作记忆：per-user 对话状态持久化';

CREATE TABLE IF NOT EXISTS claw_knowledge_access_log (
    id          BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    node_id     BIGINT UNSIGNED NOT NULL,
    session_key VARCHAR(256),
    query_text  VARCHAR(512),
    score       FLOAT,
    accessed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_node    (node_id),
    INDEX idx_session (session_key),
    INDEX idx_time    (accessed_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='知识访问记录，用于重要性自适应调整';

-- ── 3. 验证（执行后可运行此 SELECT 确认） ────────────────────
-- SELECT table_name FROM information_schema.tables
-- WHERE table_schema = 'openclaw_teamlab'
-- ORDER BY table_name;
