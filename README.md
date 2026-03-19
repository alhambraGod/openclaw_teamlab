# OpenClaw TeamLab — 项目说明

## 目录结构

```
openclaw_teamlab/
├── claw/                         # C-Si 专属 OpenClaw (Docker)  ← 入口在这里
│   ├── Makefile                 # make up / down / logs / ...
│   ├── launch.sh                # 等同于 make up
│   ├── docker-compose.yml       # claw-openclaw (port 10300) 单独模式
│   ├── docker-compose.full.yml  # 全容器模式（含 TeamLab 后端）
│   ├── config/openclaw.json     # OpenClaw 配置（模型/飞书/Cron）
│   └── workspace/               # AGENTS.md / SOUL.md / skills（agent 行为定义）
│
├── config/                      # 数据后端配置
│   ├── settings.py
│   ├── database.py
│   ├── coevo_db.py
│   └── agents.yaml              # Worker 配置 / Intent 路由 / Scheduler 任务
│
├── data_bridge/                 # 核心数据分析引擎（被 /api/agent/* 调用）
│   ├── team_context.py
│   ├── risk_engine.py
│   ├── narrative.py
│   ├── research_direction_analyzer.py
│   └── ...
│
├── gateway/                     # FastAPI REST API（Web UI 后端）
│   ├── app.py
│   └── routes/
│
├── workers/                     # Worker 池（后台 LLM 任务处理）
├── scheduler/                   # APScheduler 定时任务
├── skills/                      # Skill 定义（SKILL.md + scripts/）
├── models/                      # SQLAlchemy ORM 模型
├── web/                         # Web UI 静态文件
├── data/
│   ├── logs/                    # 运行日志（按天滚动，30 天保留）
│   ├── migrations/              # SQL 初始化脚本
│   └── seeds/                   # 默认种子数据
│
├── main.py                      # CLI 入口（./teamlab all/stop/status）
└── docs/                        # 文档
    ├── architecture.md
    ├── database.md
    ├── deployment.md
    └── development.md
```

## 快速启动

### 方式 A：启动 C-Si 专属 OpenClaw（Docker）← 主要入口

```bash
cd ~/openclaws/openclaw_teamlab/claw
make up          # 启动 OpenClaw（需先启动数据后端：make backend-start）
make up-all      # 一步启动：数据后端 + OpenClaw
make status      # 查看状态
make logs        # 查看日志
make down        # 停止
```

飞书机器人 `cli_a9383c6625a25bd2` 自动连接，向机器人发消息即可使用。

### 方式 B：启动数据后端（宿主机后台）

```bash
cd ~/openclaws/openclaw_teamlab
conda activate claw_teamlab
./teamlab all       # 启动 FastAPI (10301) + Scheduler (10302) + Workers (10310-12)
./teamlab status    # 查看状态
./teamlab stop      # 停止
```

### 方式 C：全容器部署

```bash
cd ~/openclaws/openclaw_teamlab/claw
make up-full     # 启动 claw-openclaw + claw-teamlab（全部在 Docker 中）
make down-full   # 停止
```

## 系统架构

```
飞书用户 / Web 浏览器
  │ WebSocket (App ID: cli_a9383c6625a25bd2)
  ▼
claw-openclaw 容器 (port 10300)   ← OpenClaw LLM Agent
  │ bash + curl → /api/agent/*
  ▼
openclaw_teamlab 数据后端 (port 10301)
  ├── FastAPI Gateway
  ├── Worker 池（并发 LLM 任务，port 10310-12）
  ├── Scheduler（定时任务，port 10302）
  ├── MySQL cognalign_coevo_prod  (只读，团队事实数据)
  ├── MySQL openclaw_teamlab      (读写，知识图谱/任务日志)
  └── Redis (任务队列 / Pub-Sub / 知识 watermark)
```

## 端口分配

| 端口 | 服务 |
|------|------|
| 10300 | claw-openclaw（Docker）|
| 10301 | TeamLab FastAPI Gateway |
| 10302 | Scheduler |
| 10310-10329 | Worker 池 |

## 详细文档

- **部署运维手册**：[docs/deployment.md](docs/deployment.md)
- **系统架构**：[docs/architecture.md](docs/architecture.md)
- **数据库 Schema**：[docs/database.md](docs/database.md)
