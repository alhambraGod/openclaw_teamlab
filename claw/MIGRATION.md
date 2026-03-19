# C-Si TeamLab 入口迁移指南
# 飞书机器人 cli_a9383c6625a25bd2 → 专属 OpenClaw

## 迁移目标

| 项目 | 迁移前 | 迁移后 |
|------|--------|--------|
| 飞书机器人持有者 | `openclaw_teamlab` FastAPI (port 10301) | **C-Si 专属 OpenClaw** (port 10300) |
| 消息处理方式 | 关键词路由 → 固定脚本 → 有限功能 | Pi agent ReAct 推理 → bash+curl → /api/agent/* → 智能分析 |
| 入口 App ID | `cli_a9383c6625a25bd2` | **同一个**（不换机器人，换后端） |

用户感知：**飞书机器人 ID 不变**，发消息方式不变，但回答质量从"路由到固定脚本"升级为"自主多步推理"。

---

## 架构关系（迁移后）

```
用户发飞书消息
      │
      ▼
飞书服务器 (cli_a9383c6625a25bd2)
      │  WebSocket 长连接
      ▼
C-Si 专属 OpenClaw  [Docker 容器, port 10300]
  ├── 完全独立于宿主机 OpenClaw (port 10001)
  ├── 专属 workspace: csi/workspace/
  └── Pi agent ReAct 推理循环
        │  bash + curl → /api/agent/*
        ▼
  openclaw_teamlab FastAPI (port 10301)  [数据后端]
        │  只读 CoEvo DB / 读写 TeamLab DB
        ▼
  MySQL (cognalign_coevo_prod + openclaw_teamlab)

openclaw_teamlab FastAPI (port 10301)  [保持运行，作为数据后端]
  ├── 飞书 receiver: 已停用（FEISHU_APP_ID 置空）
  ├── REST API: 继续运行（claw-openclaw 通过 host.docker.internal:10301 调用）
  ├── Workers: 继续运行
  └── Scheduler: 继续运行
```

---

## 迁移步骤（一次性操作）

### Step 1：停止旧系统的飞书 receiver

旧系统 `openclaw_teamlab/.env` 中 `FEISHU_APP_ID` 已置空，下次重启时 receiver 不会启动。

若旧系统**当前正在运行**，需重启让配置生效：

```bash
cd /Users/antonio/openclaws/openclaw_teamlab

# 停止当前运行的旧系统
python main.py stop

# 确认 FEISHU_APP_ID 已置空
grep FEISHU_APP_ID .env
# 应输出: FEISHU_APP_ID=

# 重启旧系统（只启动 web/scheduler/workers，不再启动飞书 receiver）
python main.py all
```

验证旧系统已释放飞书：

```bash
# 旧系统日志中应看到:
# "FEISHU_APP_ID / FEISHU_APP_SECRET not configured — aborting"
# 而不是 "Feishu receiver started"
grep -i "feishu" logs/openclaw_teamlab_web.log | tail -5
```

### Step 2：启动专属 OpenClaw

```bash
cd /Users/antonio/openclaws/openclaw_teamlab/claw
./launch.sh
```

launch.sh 会自动检测旧系统是否已释放飞书连接，确认后再启动。

### Step 3：验证飞书消息路由

在飞书向机器人 `cli_a9383c6625a25bd2` 发送测试消息：

```
帮我看看团队现在的情况
```

**预期响应**（新系统）：
- 专属 OpenClaw 接管，调用 `get_team_overview()` 工具
- 返回基于真实数据库数据的团队快照
- 不再出现"协作推荐"通用回复或 code 200340 错误

```
张旭华跟谁合作价值最高
```

**预期响应**（新系统）：
- 调用 `get_person_context("张旭华")` 获取其目标和 blockers
- 调用 `find_best_collaborators("张旭华")` 遍历全员打分
- 返回有数据依据的排名分析

---

## 飞书机器人配置信息

```
App ID    : cli_a9383c6625a25bd2
App Secret: 7G3U2FOki4AwKv6YYTPmmbZgXHXM6xIn
连接模式   : WebSocket（飞书开放平台 → OpenClaw 容器长连接）
配置文件   : openclaw_teamlab_csi/config/openclaw.json
```

> 飞书开放平台无需任何改动——WebSocket 模式下是 OpenClaw 主动连接飞书，
> 不是飞书 Webhook 推送，所以不需要配置回调地址。

---

## 回滚方案

若需紧急回退：

```bash
# 1. 停止专属 OpenClaw
cd /Users/antonio/openclaws/openclaw_teamlab/claw
docker compose down

# 2. 在旧系统 .env 中恢复飞书配置
# 编辑 /Users/antonio/openclaws/openclaw_teamlab/.env
# FEISHU_APP_ID=cli_a9383c6625a25bd2
# FEISHU_APP_SECRET=7G3U2FOki4AwKv6YYTPmmbZgXHXM6xIn

# 3. 重启旧系统
cd /Users/antonio/openclaws/openclaw_teamlab
python main.py stop && python main.py all
```

---

## 端口速查

| 端口 | 服务 | 说明 |
|------|------|------|
| **10300** | **claw-openclaw** | **新飞书入口，Control UI** |
| 10301 | openclaw_teamlab FastAPI | 数据后端 |
| 10302 | Scheduler | 定时任务 |
| 10001 | 主 OpenClaw | 与本系统完全独立 |
