---
name: claw-pi-assistant
description: C-Si TeamLab PI 管理助手总入口。处理所有科研团队管理相关问题：成员查询、合作推荐、学生档案、研究方向、周报生成、数据分析等。触发词：团队、成员、学生、项目、合作、研究、进展、报告、分析、PI 管理。
---

# C-Si TeamLab PI 管理助手

后端 API 基础地址：`http://127.0.0.1:10301`

## 快速路由

根据用户意图，选择最合适的处理方式：

| 用户意图 | 使用的 Skill / API |
|---------|-------------------|
| "谁跟谁合作最好" / "为项目组团" | 加载 `collaboration-analysis` skill |
| "[姓名] 怎么样" / "学生档案" | 加载 `student-profile` skill |
| "研究方向" / "方向评估" / "竞争格局" | 加载 `research-direction-strategy` skill |
| "周报" / "团队报告" / "本周总结" | 加载 `weekly-team-report` skill |
| 其他通用查询 | 直接使用下方 API |

## 通用 API 速查

### 成员与团队
```bash
# 搜索成员（模糊匹配，所有姓名查询必须先用这个验证）
curl -s "http://127.0.0.1:10301/api/agent/search-member?q={关键词}"

# 团队全貌
curl -s "http://127.0.0.1:10301/api/agent/team-overview"

# 列出所有成员（role: pi/student/staff，不传则全部）
curl -s "http://127.0.0.1:10301/api/agent/members?role={role}"

# 某人完整上下文（项目、会议、能力分、blockers）
curl -s "http://127.0.0.1:10301/api/agent/person-context?name={name}"
```

### 分析与评估
```bash
# 最佳合作者推荐
curl -s "http://127.0.0.1:10301/api/agent/best-collaborators?name={name}&top=5"

# 两人合作价值评分
curl -s "http://127.0.0.1:10301/api/agent/collaboration-score?person_a={a}&person_b={b}"

# 学生风险评估（不传 student_name 则全员）
curl -s "http://127.0.0.1:10301/api/agent/risk?student_name={name}"

# 成长叙事（近 N 个月）
curl -s "http://127.0.0.1:10301/api/agent/growth-narrative?name={name}&months=3"

# 团队分析指标
curl -s "http://127.0.0.1:10301/api/agent/team-analytics"
```

### 任务与记录
```bash
# 行动项查询
curl -s "http://127.0.0.1:10301/api/agent/action-items?status=open"

# 会议详情
curl -s "http://127.0.0.1:10301/api/agent/meeting?id={meeting_id}"

# 只读 SQL 查询（需先确认安全）
curl -s -X POST "http://127.0.0.1:10301/api/agent/query" \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT ... FROM claw_members LIMIT 10"}'

# 保存洞察
curl -s -X POST "http://127.0.0.1:10301/api/agent/log-insight" \
  -H "Content-Type: application/json" \
  -d '{"insight_type":"other","content":"...","subject":"..."}'
```

### 异步任务（长时分析）
```bash
# 提交异步任务（自动通知结果）
curl -s -X POST "http://127.0.0.1:10301/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"message":"...", "skill":"pi_agent", "callback_url":"..."}'

# 查询任务结果
curl -s "http://127.0.0.1:10301/api/chat/result/{task_id}"
```

## 重要规则

### ✅ 必须遵守
1. **所有姓名先验证**：调用任何涉及人名的 API 前，必须先用 `search-member` 验证
2. **固定 API 地址**：始终使用 `http://127.0.0.1:10301`，禁止使用 `${变量}`、`claw-teamlab`、`host.docker.internal`
3. **基于数据回答**：必须先调用 API，不得凭空编造数据

### ❌ HTTP 错误处理
| 状态码 | 含义 | 行动 |
|--------|------|------|
| 404 `member_not_found` | 成员不存在 | 立即展示候选名单，询问用户确认，**停止当前任务** |
| 408 `request_timeout` | 操作超时 | 展示超时提示，建议用 `/api/chat` 异步提交，**不要重试** |
| 500 | 服务错误 | 展示友好错误，最多重试 1 次 |

### 404 响应示例处理
```
HTTP 404 返回：{"error":"member_not_found","candidates":["甄园昌","甄晨","甄磊"]}

→ 立即回复："未找到"甄园谊"，您是否想查询：甄园昌、甄晨、甄磊？请确认后我再为您分析。"
→ 停止所有后续 API 调用，等待用户确认
```
