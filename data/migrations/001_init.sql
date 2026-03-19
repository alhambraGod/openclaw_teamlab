-- =============================================
-- OpenClaw TeamLab — Database Schema
-- MySQL 8.0+ / utf8mb4
-- =============================================

CREATE DATABASE IF NOT EXISTS openclaw_teamlab
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE openclaw_teamlab;

-- ── Students ──
CREATE TABLE IF NOT EXISTS claw_students (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    name            VARCHAR(100) NOT NULL,
    email           VARCHAR(200),
    feishu_open_id  VARCHAR(100),
    avatar_url      VARCHAR(500),
    research_area   TEXT,
    bio             TEXT,
    enrollment_date DATE,
    degree_type     ENUM('phd','master','postdoc','undergrad') DEFAULT 'phd',
    advisor_notes   TEXT,
    tags            JSON,
    status          ENUM('active','graduated','on_leave') DEFAULT 'active',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_feishu (feishu_open_id),
    INDEX idx_status (status)
) ENGINE=InnoDB;

-- ── Capability Dimensions ──
CREATE TABLE IF NOT EXISTS claw_capability_dimensions (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(100) NOT NULL UNIQUE,
    label       VARCHAR(100) NOT NULL,
    description TEXT,
    category    VARCHAR(50),
    sort_order  INT DEFAULT 0
) ENGINE=InnoDB;

-- ── Capability Scores (time-series for radar charts) ──
CREATE TABLE IF NOT EXISTS claw_capability_scores (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    student_id    INT NOT NULL,
    dimension_id  INT NOT NULL,
    score         DECIMAL(3,1) NOT NULL,
    assessed_at   DATE NOT NULL,
    assessed_by   VARCHAR(50) DEFAULT 'system',
    evidence      TEXT,
    FOREIGN KEY (student_id) REFERENCES claw_students(id) ON DELETE CASCADE,
    FOREIGN KEY (dimension_id) REFERENCES claw_capability_dimensions(id) ON DELETE CASCADE,
    INDEX idx_student_time (student_id, assessed_at)
) ENGINE=InnoDB;

-- ── Progress Events ──
CREATE TABLE IF NOT EXISTS claw_progress_events (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    student_id  INT NOT NULL,
    event_type  ENUM('paper_submitted','paper_accepted','paper_rejected',
                     'experiment_completed','milestone_reached','presentation',
                     'code_released','dataset_created','review_completed',
                     'thesis_proposal','qualification_exam','award','custom') NOT NULL,
    title       VARCHAR(500) NOT NULL,
    description TEXT,
    metadata    JSON,
    event_date  DATE NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id) REFERENCES claw_students(id) ON DELETE CASCADE,
    INDEX idx_student_date (student_id, event_date),
    INDEX idx_type (event_type)
) ENGINE=InnoDB;

-- ── Meetings ──
CREATE TABLE IF NOT EXISTS claw_meetings (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    title         VARCHAR(500),
    meeting_type  ENUM('group','individual','seminar','external') NOT NULL,
    meeting_date  DATETIME NOT NULL,
    duration_min  INT,
    attendees     JSON,
    raw_notes     TEXT,
    summary       TEXT,
    topics        JSON,
    action_items  JSON,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_date (meeting_date)
) ENGINE=InnoDB;

-- ── Research Directions ──
CREATE TABLE IF NOT EXISTS claw_research_directions (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    title             VARCHAR(500) NOT NULL,
    description       TEXT,
    source            ENUM('pi_defined','ai_suggested','meeting_derived') NOT NULL,
    status            ENUM('active','exploring','paused','completed') DEFAULT 'exploring',
    related_students  JSON,
    related_meetings  JSON,
    evidence          TEXT,
    priority          INT DEFAULT 5,
    parent_id         INT DEFAULT NULL,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (parent_id) REFERENCES claw_research_directions(id) ON DELETE SET NULL,
    INDEX idx_status (status)
) ENGINE=InnoDB;

-- ── Task Log (audit trail) ──
CREATE TABLE IF NOT EXISTS claw_task_log (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    task_id         VARCHAR(64) NOT NULL UNIQUE,
    user_id         VARCHAR(200),
    user_name       VARCHAR(100),
    source          ENUM('feishu','web','api','scheduler') NOT NULL,
    skill_used      VARCHAR(100),
    input_text      TEXT,
    result_summary  TEXT,
    result_data     JSON,
    status          ENUM('queued','running','completed','failed') NOT NULL,
    worker_id       VARCHAR(50),
    duration_ms     INT,
    error_message   TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at    TIMESTAMP NULL,
    INDEX idx_user (user_id),
    INDEX idx_status_time (status, created_at),
    INDEX idx_skill (skill_used)
) ENGINE=InnoDB;

-- ── Collaboration Recommendations ──
CREATE TABLE IF NOT EXISTS claw_collaboration_recommendations (
    id                    INT AUTO_INCREMENT PRIMARY KEY,
    student_a_id          INT NOT NULL,
    student_b_id          INT NOT NULL,
    complementarity_score DECIMAL(4,2),
    overlap_score         DECIMAL(4,2),
    research_idea         TEXT,
    rationale             TEXT,
    status                ENUM('suggested','accepted','in_progress','completed','dismissed') DEFAULT 'suggested',
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (student_a_id) REFERENCES claw_students(id) ON DELETE CASCADE,
    FOREIGN KEY (student_b_id) REFERENCES claw_students(id) ON DELETE CASCADE,
    INDEX idx_pair (student_a_id, student_b_id)
) ENGINE=InnoDB;

-- ── Research Trends (self-evolution cache) ──
CREATE TABLE IF NOT EXISTS claw_research_trends (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    domain            VARCHAR(200) NOT NULL,
    trend_title       VARCHAR(500),
    summary           TEXT,
    source_urls       JSON,
    relevance_score   DECIMAL(3,2),
    matched_students  JSON,
    matched_directions JSON,
    discovered_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notified          BOOLEAN DEFAULT FALSE,
    INDEX idx_domain_date (domain, discovered_at),
    INDEX idx_notified (notified)
) ENGINE=InnoDB;

-- ── Email Digest Log ──
CREATE TABLE IF NOT EXISTS claw_email_digests (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    recipient_email VARCHAR(200) NOT NULL,
    digest_type     ENUM('daily','weekly') NOT NULL,
    subject_line    VARCHAR(500),
    content_hash    VARCHAR(64),
    trend_ids       JSON,
    sent_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_recipient_date (recipient_email, sent_at)
) ENGINE=InnoDB;

-- ── PI Configuration ──
CREATE TABLE IF NOT EXISTS claw_pi_config (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    config_key    VARCHAR(100) NOT NULL UNIQUE,
    config_value  JSON NOT NULL,
    description   VARCHAR(500),
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- ── Conversation History (for context-aware responses) ──
CREATE TABLE IF NOT EXISTS claw_conversations (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    user_id     VARCHAR(200) NOT NULL,
    role        ENUM('user','assistant','system') NOT NULL,
    content     TEXT NOT NULL,
    skill_used  VARCHAR(100),
    metadata    JSON,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user_time (user_id, created_at)
) ENGINE=InnoDB;
