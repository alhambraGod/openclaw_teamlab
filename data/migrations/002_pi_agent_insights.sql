-- Migration 002: PI Agent Insights Table
-- 用于持久化全球研究洞见、跨项目协作分析等 AI 生成的洞见

CREATE TABLE IF NOT EXISTS `claw_pi_agent_insights` (
  `id`           BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  `insight_type` VARCHAR(64)  NOT NULL DEFAULT 'other'
                 COMMENT 'global_research|cross_project|risk|collaboration|other',
  `subject`      VARCHAR(255) DEFAULT NULL
                 COMMENT '洞见主题/标题',
  `content`      MEDIUMTEXT   NOT NULL,
  `metadata`     JSON         DEFAULT NULL
                 COMMENT '额外结构化数据（paper_titles、scan_date 等）',
  `created_at`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX `idx_type_created` (`insight_type`, `created_at`),
  INDEX `idx_subject`      (`subject`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='PI Agent 洞见持久化（全球研究热点、跨项目协作等）';
