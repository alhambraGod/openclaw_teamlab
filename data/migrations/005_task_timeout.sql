-- Migration 005: Task Timeout Support
-- Adds timeout status, callback_url and timeout_at to claw_task_log.
-- Safe to run multiple times (uses IF NOT EXISTS / MODIFY COLUMN).

-- 1. Extend status ENUM to include 'timeout'
ALTER TABLE `claw_task_log`
  MODIFY COLUMN `status`
    ENUM('queued','running','completed','failed','timeout')
    NOT NULL DEFAULT 'queued';

-- 2. Add callback_url column (stores OpenClaw callback endpoint for async reply)
ALTER TABLE `claw_task_log`
  ADD COLUMN IF NOT EXISTS `callback_url` VARCHAR(500) NULL COMMENT 'POST result here on complete/timeout (e.g. OpenClaw Feishu callback)';

-- 3. Add timeout_at column (populated when task times out or watchdog kills it)
ALTER TABLE `claw_task_log`
  ADD COLUMN IF NOT EXISTS `timeout_at` TIMESTAMP NULL DEFAULT NULL COMMENT 'Timestamp when task was killed due to timeout';

-- 4. Index for watchdog query: running/queued tasks older than N minutes
CREATE INDEX IF NOT EXISTS `idx_task_status_created`
  ON `claw_task_log` (`status`, `created_at`);

-- Verify
SELECT 'Migration 005 applied: task timeout support' AS result;
