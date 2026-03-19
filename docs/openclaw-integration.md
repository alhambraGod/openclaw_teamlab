# OpenClaw 与 TeamLab 集成指南

## 架构概览

- **OpenClaw** (http://127.0.0.1:10300)：飞书机器人 + 控制台 UI，用户对话入口
- **TeamLab** (http://127.0.0.1:10301)：数据后端，提供 HTTP 接口
- **Worker 池** (10310-10329)：异步任务处理，提高并发

## 1. OpenClaw 调度的 HTTP 接口（完备清单）

### Agent API（/api/agent/）

| 端点 | 方法 | 说明 |
|------|------|------|
| /api/agent/team-overview | GET | 团队全景 |
| /api/agent/person-context | GET | 某人完整上下文 ?name= |
| /api/agent/best-collaborators | GET | 最佳合作者 ?name=&top=5 |
| /api/agent/collaboration-score | GET | 两人合作价值 ?person_a=&person_b= |
| /api/agent/risk | GET | 风险评估 ?student_name= |
| /api/agent/growth-narrative | GET | 成长叙事 ?name=&months=3 |
| /api/agent/meeting-details | GET | 会议详情 |
| /api/agent/team-analytics | GET | 团队分析指标 |
| /api/agent/members | GET | 列出成员 ?role= |
| /api/agent/query | POST | 只读 SQL，Body: {sql} |
| /api/agent/action-items | GET | 待办 ?status=open,stale |
| /api/agent/log-insight | POST | 持久化洞见 |
| /api/agent/save-collaboration | POST | 保存协作推荐 |

### 异步任务 API（/api/chat）

| 端点 | 方法 | 说明 |
|------|------|------|
| /api/chat | POST | 提交任务，立即返回 task_id |
| /api/chat/result/{task_id} | GET | 轮询任务结果 |
| /api/chat/history/{user_id} | GET | 会话历史 |

### 其他

| 端点 | 说明 |
|------|------|
| /health | 健康检查 |
| /api/coevo/members | CoEvo 成员 |
| /api/collaborations/network | 协作网络图 |

## 2. 异步任务流程（提高并发）

当 OpenClaw 收到用户消息时，可提交到 TeamLab 异步处理，自身立即释放以处理其他请求。

### 方式 A：轮询

```bash
# 1. 提交任务
curl -X POST "http://127.0.0.1:10301/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"message":"甄园谊老师跟谁合作最好","user_id":"feishu_xxx","source":"feishu"}'
# 返回: {"task_id":"abc123","status":"queued"}

# 2. 轮询结果
curl "http://127.0.0.1:10301/api/chat/result/abc123"
```

### 方式 B：callback_url（任务完成后主动推送）

```bash
curl -X POST "http://127.0.0.1:10301/api/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "message":"甄园谊老师跟谁合作最好",
    "user_id":"feishu_xxx",
    "source":"feishu",
    "callback_url":"https://your-openclaw/callback"
  }'
```

任务完成后，Worker 会 POST 到 `callback_url`，Body 示例：

```json
{
  "task_id": "abc123",
  "status": "completed",
  "result_summary": "根据分析，甄园谊老师...",
  "result_data": {...},
  "error_message": null
}
```

### 方式 C：WebSocket

订阅 `ws://127.0.0.1:10301/ws`，Gateway 在任务完成时会广播 `task_update` 事件。

## 3. 用户入口

- **飞书**：通过 OpenClaw 飞书通道，消息进入 OpenClaw
- **控制台**：http://127.0.0.1:10300 对话界面

两者均可由 OpenClaw 将任务提交到 TeamLab `/api/chat` 异步处理。
