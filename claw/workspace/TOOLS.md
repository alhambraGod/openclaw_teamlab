# TOOLS.md — Agent 工具与服务索引

## ⚠️ 强制：TeamLab API 地址

**固定使用** `http://127.0.0.1:10301`。**严禁**使用变量语法（`${TEAMLAB_BASE_URL:-...}` 会破坏 URL）、`claw-teamlab`、`csi-teamlab` 或 `host.docker.internal`。

## 调用方式（OpenClaw 最佳实践）

Agent 使用 **bash + curl** 调用 TeamLab Agent API，无需 MCP。

**Base URL**：`http://127.0.0.1:10301`（固定值；TeamLab Docker 容器将 10301 端口映射到宿主机，始终可达）

## Agent API 端点速查（/api/agent/）

| 我想做什么 | curl 示例 |
|-----------|----------|
| 获取团队全景 | `curl -s "http://127.0.0.1:10301/api/agent/team-overview"` |
| 某人完整上下文 | `curl -s "http://127.0.0.1:10301/api/agent/person-context?name=张旭华"` |
| 最佳合作者推荐 | `curl -s "http://127.0.0.1:10301/api/agent/best-collaborators?name=张旭华&top=5"` |
| 两人合作价值分析 | `curl -s "http://127.0.0.1:10301/api/agent/collaboration-score?person_a=张旭华&person_b=李四"` |
| 风险评估（单人/全员） | `curl -s "http://127.0.0.1:10301/api/agent/risk?student_name=张旭华"` 或 `?student_name=` 为空则全员 |
| 成长叙事 | `curl -s "http://127.0.0.1:10301/api/agent/growth-narrative?name=张旭华&months=3"` |
| 会议详情 | `curl -s "http://127.0.0.1:10301/api/agent/meeting-details?recent_n=5"` |
| 团队分析指标 | `curl -s "http://127.0.0.1:10301/api/agent/team-analytics"` |
| 列出成员 | `curl -s "http://127.0.0.1:10301/api/agent/members?role=student"` |
| 自定义 SQL 查询 | `curl -s -X POST "http://127.0.0.1:10301/api/agent/query" -H "Content-Type: application/json" -d '{"sql":"SELECT ..."}'` — 见 TEAMLAB_SCHEMA.md |
| 待办事项 | `curl -s "http://127.0.0.1:10301/api/agent/action-items?status=open,stale"` |
| **全球研究热点** | `curl -s "http://127.0.0.1:10301/api/agent/global-research?days=7"` |
| **按主题过滤热点** | `curl -s "http://127.0.0.1:10301/api/agent/global-research?topic=大模型对齐&days=14"` |
| **跨项目协作机会** | `curl -s "http://127.0.0.1:10301/api/agent/cross-project"` |
| **手动触发全球扫描** | `curl -s -X POST "http://127.0.0.1:10301/api/agent/scan-global-research"` |
| **团队知识库** | `curl -s "http://127.0.0.1:10301/api/agent/team-knowledge?subject=张三&days=30"` |
| **系统进化报告** | `curl -s "http://127.0.0.1:10301/api/agent/evolution-report"` |
| 持久化洞见 | `curl -s -X POST "http://127.0.0.1:10301/api/agent/log-insight" -H "Content-Type: application/json" -d '{"insight_type":"other","content":"发现内容","subject":"标题"}'` |
| 保存协作推荐 | `curl -s -X POST "http://127.0.0.1:10301/api/agent/save-collaboration" -H "Content-Type: application/json" -d '{"person_a":"张旭华","person_b":"李四","score":85,"reasoning":"分析理由","ideas":["方向1"]}'` |

## 其他 TeamLab 端点（统一使用 http://127.0.0.1:10301）

| 用途 | curl 示例 |
|------|----------|
| 协作网络图 | `curl -s "http://127.0.0.1:10301/api/collaborations/network"` |
| 协作列表 | `curl -s "http://127.0.0.1:10301/api/collaborations"` |
| CoEvo 成员 | `curl -s "http://127.0.0.1:10301/api/coevo/members"` |
| 学生详情 | `curl -s "http://127.0.0.1:10301/api/coevo/claw_students/{id}"` |
| 学生成长叙事 | `curl -s "http://127.0.0.1:10301/api/coevo/claw_students/{id}/narrative"` |

**注意**：所有 TeamLab 后端接口统一使用 `http://127.0.0.1:10301`，禁止使用 `csi-teamlab` 或 `host.docker.internal`。

**若 `/api/agent/*` 返回 404**：需重启 TeamLab 后端（`./teamlab stop && ./teamlab all` 或 `make backend-stop && make backend-start`），启动日志应出现 `Agent API (/api/agent/*) registered`。

## 异步任务（提高并发）

OpenClaw 可将用户消息提交到 TeamLab，由 Worker 异步处理，自身继续处理其他请求：

```bash
# 提交任务，立即返回
curl -X POST "http://127.0.0.1:10301/api/chat" -H "Content-Type: application/json" \
  -d '{"message":"甄园谊老师跟谁合作最好","user_id":"feishu_xxx","source":"feishu"}'
# → {"task_id":"abc123","status":"queued"}

# 轮询结果
curl "http://127.0.0.1:10301/api/chat/result/abc123"
```

可选 `callback_url`：任务完成后 Worker 会 POST 结果到该 URL。详见 `docs/openclaw-integration.md`。

可选 `email`：任务完成/超时后 Worker 会将结果发送到指定邮箱（仅在用户明确提供邮箱时使用）。

```bash
# 异步任务 + 邮件通知
curl -X POST "http://127.0.0.1:10301/api/chat" -H "Content-Type: application/json" \
  -d '{"message":"分析团队研究方向","user_id":"feishu_xxx","source":"feishu","email":"user@example.com"}'
```

## 邮件发送（用户指定邮箱时）

仅在用户 **明确提供邮箱地址** 时调用，发送前须告知用户「将发送至 xxx@yyy.com」。

```bash
# 即时发送邮件
curl -s -X POST "http://127.0.0.1:10301/api/agent/send-email" \
  -H "Content-Type: application/json" \
  -d '{
    "to": "user@example.com",
    "subject": "TeamLab 周报",
    "content": "本周研究进展：...",
    "html": false
  }'
# → {"status":"sent","to":"user@example.com","subject":"TeamLab 周报"}
```

**规则**：
- 地址不合法（无 `@`）会立即返回 422
- 发送失败返回 502，提示用户稍后重试
- 主动推送报告/风险预警时，如用户已提供邮箱，优先通过邮件推送

## 服务端口速查（10300-10399）

| 端口 | 服务 | 说明 |
|------|------|------|
| **10300** | claw-openclaw | Control UI `http://127.0.0.1:10300`，飞书 WebSocket 入口 |
| **10301** | openclaw_teamlab | TeamLab 数据后端 FastAPI `http://127.0.0.1:10301` |
| **10302** | Scheduler | 定时任务 `http://127.0.0.1:10302/health` |
| **10302** | Scheduler | 定时任务引擎 |
| **10310-10329** | Worker 池 | 内部端口 |

## 飞书入口

- **机器人 App ID**: `cli_a9383c6625a25bd2`（TeamLab 专属）
- **连接方式**: WebSocket 长连接
- **持有者**: 本专属 OpenClaw 容器

## 数据权限

- **cognalign_coevo_prod**：只读
- **openclaw_teamlab**：读写（洞见、协作推荐、行动项等）
