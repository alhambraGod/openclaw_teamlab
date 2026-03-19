# CoEvo 数据库 Schema — Agent SQL 查询参考

通过 `/api/agent/query` 执行 **只读** SQL（SELECT），可灵活回答各类问题。
系统会自动附加 `LIMIT 100`（若 SQL 中已有 LIMIT 则不再追加）。

## ⚠️ 注意事项

- 表名和列名**必须严格按照下方文档**，不可凭记忆猜测
- `projects` 表**没有** `name` 列，正确列名是 `project_name`
- `projects` 表**没有** `status` 列，用 `is_active = 1` 过滤活跃项目
- `users` 表**没有** `name` 列，正确列名是 `username`
- `meetings` 表不叫 `claw_meetings`
- `collaboration_recommendations` 表不叫 `claw_collaboration_recommendations`

## 核心表结构

### users
| 列 | 类型 | 说明 |
|----|------|------|
| id | bigint | 主键 |
| username | varchar | **姓名**（不叫 name） |
| email | varchar | |
| role | enum | student, teacher, researcher, pm |
| is_active | tinyint | 1=在队 |
| bio | text | 简介 |
| avatar_url | varchar | |
| last_login_at | timestamp | |

### projects
| 列 | 类型 | 说明 |
|----|------|------|
| id | bigint | 主键 |
| **project_name** | varchar | **项目名**（不叫 name） |
| project_code | varchar | 项目编号 |
| description | text | 项目描述 |
| creator_user_id | bigint | FK → users.id |
| max_members | int | |
| member_count | int | 成员数 |
| **is_active** | tinyint | **1=活跃**（没有 status 列） |
| settings | json | |

### project_members
| 列 | 类型 | 说明 |
|----|------|------|
| id | bigint | 主键 |
| user_id | bigint | FK → users.id |
| project_id | bigint | FK → projects.id |
| project_role | enum | 项目角色 |
| project_auth | enum | 权限 |
| display_name | varchar | |
| quarterly_goal | text | 季度目标 |
| short_term_goal | text | 近期目标 |
| joined_at | timestamp | |

### meetings
| 列 | 类型 | 说明 |
|----|------|------|
| id | bigint | 主键 |
| project_id | bigint | FK → projects.id |
| meeting_name | varchar | |
| meeting_time | timestamp | |
| creator_user_id | bigint | |
| phase | enum | |
| status | enum | |
| overall_summary | text | 会议总结 |
| is_active | tinyint | |

### meeting_reports
| 列 | 类型 | 说明 |
|----|------|------|
| id | bigint | 主键 |
| meeting_id | bigint | FK → meetings.id |
| user_id | bigint | FK → users.id |
| phase | enum | pre/post |
| task_items | text | 完成任务 |
| key_blockers | text | 阻塞问题 |
| next_week_plan | text | 下周计划 |
| remarks | text | |
| dialogue_detail | text | |
| core_viewpoints | text | |
| issues_recorded | text | |

### collaboration_recommendations
| 列 | 类型 | 说明 |
|----|------|------|
| id | bigint | 主键 |
| project_id | bigint | FK → projects.id |
| requester_user_id | bigint | 被分析的合作主体 |
| target_user_ids | json | 推荐合作者 id 数组 |
| mode | enum | |
| collaboration_direction | text | 合作方向 |
| collaboration_suggestion | text | |
| expected_output | text | |
| best_partner_analysis | json | |
| raw_llm_response | text | |
| status | enum | pending, completed 等 |

### research_plans
| 列 | 类型 | 说明 |
|----|------|------|
| id | bigint | 主键 |
| project_id | bigint | FK → projects.id |
| creator_user_id | bigint | |
| plan_name | varchar | |
| total_cycles | int | |
| nodes | json | |
| final_goal | text | |

### agent_memories
| 列 | 类型 | 说明 |
|----|------|------|
| id | bigint | 主键 |
| （查询时使用 SELECT * FROM agent_memories LIMIT 5 查看实际列）| | |

## 常见错误 & 正确写法

❌ 错误：
```sql
SELECT p.name FROM projects p WHERE p.status = 'active'
```

✅ 正确：
```sql
SELECT p.project_name FROM projects p WHERE p.is_active = 1
```

❌ 错误：
```sql
SELECT u.name FROM users u
```

✅ 正确：
```sql
SELECT u.username FROM users u
```

❌ 错误：
```sql
SELECT * FROM claw_meetings
```

✅ 正确：
```sql
SELECT * FROM meetings
```

## 智能查询策略

1. **先理解问题**：提取主体（人、项目）、关系（合作、参与）、约束（角色、时间）。
2. **优先用专用 API**：`/api/agent/team-overview`、`global-research` 等可以 <1s 拿到结构化数据。
3. **SQL 补充**：只有当专用 API 不能满足需求时，才写 SQL。
4. **控制步数**：能用 1–2 次调用完成的，不要拆成 5 次。

### 示例：查项目列表

```sql
SELECT p.project_name, p.description, p.member_count
FROM projects p
WHERE p.is_active = 1
ORDER BY p.member_count DESC
```

### 示例：查某项目成员

```sql
SELECT u.username, pm.project_role, pm.quarterly_goal
FROM project_members pm
JOIN users u ON u.id = pm.user_id
JOIN projects p ON p.id = pm.project_id
WHERE p.project_name LIKE '%认知对齐%' AND p.is_active = 1
```

### 示例：合作价值查询

```sql
SELECT p.project_name, u.username as partner, cr.best_partner_analysis
FROM collaboration_recommendations cr
JOIN projects p ON p.id = cr.project_id AND p.is_active = 1
JOIN users u ON JSON_CONTAINS(cr.target_user_ids, CAST(u.id AS JSON), '$')
JOIN users req ON req.id = cr.requester_user_id
WHERE req.username LIKE '%甄园昌%' AND cr.status = 'completed'
ORDER BY cr.created_at DESC
```
