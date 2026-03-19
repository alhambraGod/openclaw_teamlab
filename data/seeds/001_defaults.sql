-- Default capability dimensions
USE openclaw_teamlab;

INSERT INTO claw_capability_dimensions (name, label, description, category, sort_order) VALUES
('literature_mastery', '文献功底', '对领域文献的广度和深度掌握', 'research', 1),
('experimental_design', '实验设计', '实验方案设计与执行能力', 'research', 2),
('data_analysis', '数据分析', '数据处理、统计分析与可视化', 'technical', 3),
('coding_ability', '编程能力', '编程实现与工程能力', 'technical', 4),
('academic_writing', '学术写作', '论文撰写与学术表达', 'communication', 5),
('presentation', '汇报表达', '学术报告与口头表达能力', 'communication', 6),
('innovation', '创新思维', '提出新想法和解决问题的创造力', 'research', 7),
('collaboration', '协作能力', '团队合作与跨领域沟通', 'soft_skill', 8)
ON DUPLICATE KEY UPDATE label=VALUES(label);

-- Default PI config
INSERT INTO claw_pi_config (config_key, config_value, description) VALUES
('team_name', '"AI Research Lab"', '课题组名称'),
('tracked_domains', '["artificial intelligence", "machine learning", "natural language processing", "computer vision"]', '跟踪的研究领域'),
('email_recipients', '[]', 'PI及核心成员邮箱列表'),
('digest_frequency', '"daily"', '邮件摘要频率'),
('radar_dimensions', '["literature_mastery","experimental_design","data_analysis","coding_ability","academic_writing","presentation","innovation","collaboration"]', '雷达图维度选择')
ON DUPLICATE KEY UPDATE config_value=VALUES(config_value);
