# OpenClaw TeamLab — 部署手册

## 架构

```
用户（飞书 / 浏览器）
      │
      ▼
┌─────────────────────────┐
│  claw-openclaw  :10300  │  Docker 容器
│  飞书机器人 + Web UI    │
└──────────┬──────────────┘
           │ HTTP /api/agent/*
           ▼
┌─────────────────────────┐
│  claw-teamlab   :10301  │  Docker 容器（全容器模式）
│  Scheduler      :10302  │     或 宿主机进程（混合模式）
│  Workers  :10310-10329  │
└──────────┬──────────────┘
           │
           ▼
     MySQL + Redis（宿主机）
```

| 模式 | OpenClaw | Agent | 启动命令 |
|------|----------|-------|---------|
| **全容器（推荐生产）** | Docker | Docker | `make up-all` |
| **混合模式（开发调试）** | Docker | 宿主机 | `make claw start` + `make backend start` |

---

## 端口

| 端口 | 服务 |
|------|------|
| **10300** | OpenClaw（Web UI + 飞书机器人） |
| **10301** | TeamLab API |
| **10302** | Scheduler |
| **10310–10329** | Worker 池（内部） |

---

## Skill 架构与 Docker 隔离

### OpenClaw Skill 类型

OpenClaw UI 的"技能"页面将 Skill 分为三类：

| 类型 | 路径（容器内） | 管理方式 | 特点 |
|------|--------------|---------|------|
| **Built-in Skills** | `/app/skills/` | OpenClaw 镜像内置 | 随 openclaw 版本更新，不可修改 |
| **Installed Skills** | `/home/node/.openclaw/skills/` | `openclaw-managed`，项目挂载 | **项目专属，推荐方式**，UI 显示为 INSTALLED SKILLS |
| **Extra Skills** | `/home/node/.openclaw/workspace/skills/` | 工作区文件 | 零配置，UI 显示为 EXTRA SKILLS，集成度低 |

**本项目使用 Installed Skills**，与 `omniscientist-research` 等同级，在 OpenClaw UI 中显示为 `INSTALLED SKILLS`。

### 目录结构

```
openclaw_teamlab/
├── claw/                          ← Docker OpenClaw 专属目录
│   ├── config/                    → 容器内 /home/node/.openclaw/
│   │   ├── openclaw.json          # OpenClaw 主配置
│   │   ├── agents/                # Agent 会话状态
│   │   └── feishu/                # 飞书配置
│   ├── skills/                    → 容器内 /home/node/.openclaw/skills/  ← INSTALLED SKILLS
│   │   ├── claw-pi-assistant/     # 总入口（路由分发）
│   │   ├── collaboration-analysis/
│   │   ├── student-profile/
│   │   ├── research-direction-strategy/
│   │   └── weekly-team-report/
│   └── workspace/                 → 容器内 /home/node/.openclaw/workspace/
│       ├── AGENTS.md              # Agent 角色定义
│       ├── IDENTITY.md / SOUL.md  # Agent 个性配置
│       └── TEAMLAB_API.md         # 后端 API 文档（Agent 阅读）
└── skills/                        ← TeamLab 后端 Worker Skills（Python，与 OpenClaw 无关）
    ├── pi_agent/                  # PI 管理 Worker 主技能
    └── ...
```

### 隔离机制

Docker OpenClaw 容器**完全隔离**于宿主机 `~/.openclaw`：

| 挂载 | 说明 |
|------|------|
| `claw/config/` → 容器 `/home/node/.openclaw/` | OpenClaw 主配置 |
| `claw/skills/` → 容器 `/home/node/.openclaw/skills/` | **Installed Skills（项目专属）** |
| `claw/workspace/` → 容器 `/home/node/.openclaw/workspace/` | 工作区上下文文档 |
| 宿主机 `~/.openclaw/` | **不挂载**，容器完全不可见 |

### 主 Skill：teamlab-pi-agent

`teamlab-pi-agent` 是系统的**主入口 Skill**，封装了完整的 PI Agent 调用逻辑：

```
claw/skills/teamlab-pi-agent/
├── SKILL.md           # 描述 + 使用方式（OpenClaw Agent 读取）
└── scripts/
    └── teamlab.sh     # 核心脚本：任务提交 + 异步/同步模式 + 飞书适配
```

**脚本自动处理**：
- 后端 URL 探测（混合模式 → 全容器模式 → 兜底）
- 飞书渠道自动切换 fire-and-forget 模式
- 同步轮询（最多 90s），超时自动转异步
- 任务结果格式化输出

**调用示例**：
```bash
# OpenClaw Agent 飞书调用（自动异步）
$SKILLS_DIR/teamlab-pi-agent/scripts/teamlab.sh \
  "甄园昌老师跟谁合作最有价值" \
  --user "feishu:ou_xxx" \
  --channel "feishu"

# Web UI / CLI（同步轮询）
$SKILLS_DIR/teamlab-pi-agent/scripts/teamlab.sh "生成本周团队周报"
```

### 添加新 Skill

1. 在 `claw/skills/` 下创建新目录，添加 `SKILL.md`（YAML frontmatter 含 `name` 和 `description`）
2. 可选：添加 `scripts/` 目录存放可执行脚本
3. 重启 OpenClaw 容器：`make claw restart`
4. 新 Skill 自动在 OpenClaw UI 的 **INSTALLED SKILLS** 中显示

---

## 首次部署

```bash
cd ~/openclaws/openclaw_teamlab

# 1. 创建 Python 环境（混合模式需要；全容器模式不需要）
conda create -n claw_teamlab python=3.12 -y
conda activate claw_teamlab
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 必填：MYSQL_HOST/USER/PASSWORD/DATABASE、COEVO_DB_URL、REDIS_HOST、LLM_BASE_URL/KEY/MODEL

# 3. 初始化数据库（仅首次）
python main.py init-db
```

---

## 启动

所有命令在 `claw/` 目录执行：

```bash
cd ~/openclaws/openclaw_teamlab/claw
```

### 全容器模式（推荐）

OpenClaw 和 Agent 均在 Docker，容器间通过内部网络通信。

```bash
make up-all    # 构建 Agent 镜像 → 拉取 OpenClaw 镜像 → 启动全部容器
make stop      # 停止全部
```

### 混合模式（开发调试）

OpenClaw 在 Docker，Agent 在宿主机，代码修改直接生效无需重建镜像。

```bash
make backend start    # 启动宿主机 Agent
make claw start       # 启动 Docker OpenClaw
make stop             # 停止全部
```

---

## 命令速查

### 全局命令

| 命令 | 说明 |
|------|------|
| `make up-all` | **全部启动**（全容器模式） |
| `make stop` | 停止所有服务 |
| `make health` | 检查端口是否在线 |
| `make status` | 查看 Docker 容器状态 |
| `make logs` | 所有日志概览 |

### `make claw <动作>` — OpenClaw 容器

| 命令 | 说明 |
|------|------|
| `make claw start` | 启动（单独启动时，Agent 需已在运行） |
| `make claw stop` | 停止 |
| `make claw restart` | 重启（配置变更后用） |
| `make claw status` | 容器状态 |
| `make claw logs` | 实时日志 |

### `make agent <动作>` — Agent Docker 容器

| 命令 | 说明 |
|------|------|
| `make agent start` | 构建镜像并启动 |
| `make agent stop` | 停止 |
| `make agent restart` | 重启 |
| `make agent status` | 容器状态 |
| `make agent logs` | 日志（`data/logs/teamlab_web.log`） |

### `make backend <动作>` — 宿主机 Agent

| 命令 | 说明 |
|------|------|
| `make backend start` | 后台启动（混合模式） |
| `make backend stop` | 停止 |
| `make backend restart` | 重启 |
| `make backend status` | 进程状态 |
| `make backend logs` | 日志（与 `agent logs` 相同） |

> **注**：`agent logs` 和 `backend logs` 展示相同内容（`data/logs/`），因为无论哪种启动方式，日志文件路径固定不变。

### 飞书与配对

| 命令 | 说明 |
|------|------|
| `make feishu-pair CODE=xxx` | 批准飞书配对码 |
| `make feishu-pair-list` | 查看已配对列表 |
| `make approve-device` | 批准 Control UI 配对 |
| `make reset-sessions` | 清除会话缓存 |

### 维护

| 命令 | 说明 |
|------|------|
| `make pull` | 更新 OpenClaw 镜像 |
| `make clean-logs` | 清理 30 天前旧日志 |
| `make shell-oc` | 进入 OpenClaw 容器 |

---

## 飞书配对

1. 执行 `make up-all` 或 `make claw start` 启动服务
2. 在飞书找到机器人，发送任意消息
3. 机器人回复配对码（如 `123-456`）
4. 执行：`make feishu-pair CODE=123-456`
5. 验证：`make claw logs` 中出现 `[feishu] connected`

---

## 日志

| 来源 | 查看方式 |
|------|---------|
| OpenClaw 容器 | `make claw logs` |
| Agent 日志文件 | `make agent logs` 或 `make backend logs` |
| 所有概览 | `make logs` |

日志文件路径（`data/logs/`）：

| 文件 | 内容 |
|------|------|
| `teamlab_web.log` | FastAPI Gateway |
| `teamlab_scheduler.log` | Scheduler |
| `teamlab_workers.log` | Worker 池 |

---

## 故障排查

### OpenClaw 无法启动

```bash
make claw logs        # 查看错误
make claw restart     # 重启尝试恢复
```

### TeamLab API 不响应

```bash
make health              # 检查 10301 端口
make agent status        # Docker 模式：容器状态
make backend status      # 宿主机模式：进程状态
make agent restart       # 重启 Docker Agent
make backend restart     # 重启宿主机 Agent
```

### 飞书有消息但无回复

1. `make health` 确认 10301 端口 OK
2. `make claw logs` 查看是否有报错
3. `make reset-sessions && make claw restart`，飞书重新发消息

### 端口被占用

```bash
lsof -i :10300 && lsof -i :10301
kill <pid>
make up-all
```

---

## macOS 开机自启（可选）

用 launchd 守护宿主机 Agent（混合模式）：

```bash
cat > ~/Library/LaunchAgents/com.openclaw.teamlab.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>             <string>com.openclaw.teamlab</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/Caskroom/miniconda/base/envs/claw_teamlab/bin/python</string>
        <string>/Users/antonio/openclaws/openclaw_teamlab/main.py</string>
        <string>all</string>
    </array>
    <key>WorkingDirectory</key>  <string>/Users/antonio/openclaws/openclaw_teamlab</string>
    <key>KeepAlive</key>         <true/>
    <key>RunAtLoad</key>         <true/>
    <key>StandardOutPath</key>   <string>/Users/antonio/openclaws/openclaw_teamlab/data/logs/launchd.log</string>
    <key>StandardErrorPath</key> <string>/Users/antonio/openclaws/openclaw_teamlab/data/logs/launchd.log</string>
</dict>
</plist>
EOF

launchctl load ~/Library/LaunchAgents/com.openclaw.teamlab.plist
```

---

## 配置文件

| 文件 | 用途 |
|------|------|
| `.env` | DB、Redis、LLM 配置 |
| `claw/config/openclaw.json` | OpenClaw 配置（飞书、Agent URL） |
| `claw/docker-compose.yml` | 仅 OpenClaw 容器（混合模式） |
| `claw/docker-compose.full.yml` | OpenClaw + Agent 容器（全容器模式） |
| `claw/Makefile` | 所有管理命令入口 |
