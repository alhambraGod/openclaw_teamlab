# Development Guide

## Project Structure

```
openclaw_teamlab/
├── main.py                     # CLI 入口（web / scheduler / workers / all 等）
├── start.sh                    # Bash 启动脚本
├── requirements.txt            # Python 依赖
├── .env                        # 环境变量（不提交，参考 .env.example）
├── .env.example                # 环境变量模板
├── Dockerfile.teamlab          # TeamLab Agent Docker 构建文件
│
├── config/
│   ├── settings.py             # Settings 类（读取全部环境变量）
│   ├── database.py             # Async MySQL (SQLAlchemy) + Redis 连接管理
│   ├── coevo_db.py             # CoEvo 只读数据库连接（独立会话工厂）
│   └── agents.yaml             # Worker 池配置 + 意图路由规则 + 调度任务定义
│
├── models/
│   ├── __init__.py             # 主库 SQLAlchemy ORM 模型（openclaw_teamlab）
│   └── coevo.py                # CoEvo 只读 ORM 模型（cognalign_coevo_prod）
│
├── gateway/
│   ├── app.py                  # FastAPI 应用工厂 + lifespan
│   │                           # 启动 5 个并发队列消费者 + Redis Pub/Sub 监听器
│   ├── websocket.py            # WebSocket 连接管理器
│   └── routes/
│       ├── api.py              # 主路由（汇总所有子路由）
│       ├── agent_api.py        # /api/agent/* OpenClaw 调度接口
│       ├── knowledge.py        # /api/knowledge/* 知识图谱管理接口
│       ├── chat.py             # /api/chat 任务提交 + 轮询（含 ETA 预估）
│       ├── system.py           # /api/system/* 健康、配置、调度管理代理
│       ├── dashboard.py        # /api/dashboard/* 概览数据
│       ├── claw_students.py         # /api/claw_students/* CRUD + 雷达/时间线
│       ├── claw_meetings.py         # /api/claw_meetings/* CRUD
│       ├── directions.py       # /api/directions/* 研究方向树
│       ├── collaborations.py   # /api/collaborations/* 协作网络图
│       └── coevo.py            # /api/coevo/* CoEvo 数据查询
│
├── workers/
│   ├── worker.py               # 单个 Worker FastAPI 微服务
│   │                           # 含 KnowledgeRetriever 注入 + 进度 Pub/Sub 发布
│   ├── pool.py                 # Worker 池管理（启动/停止/健康/扩缩容）
│   ├── dispatcher.py           # 任务分发（找空闲 Worker 或入队）
│   ├── skill_loader.py         # 加载 SKILL.md + scripts（含 async def 工具函数）
│   └── tool_executor.py        # 工具调用执行器（LLM function calling 循环）
│
├── knowledge/                  # ★ 分层知识存储系统（新增）
│   ├── __init__.py             # 导出 EmbeddingService / KnowledgeStore /
│   │                           #   KnowledgeRetriever / MemoryManager
│   ├── embedder.py             # 向量嵌入服务（text-embedding-3-small，优雅降级）
│   ├── store.py                # KnowledgeStore：L2/L3/L4 CRUD + 语义检索
│   ├── retriever.py            # KnowledgeRetriever：四步检索管线
│   └── memory.py               # MemoryManager：L0/L5 工作记忆，Redis+MySQL 双层
│
├── roles/                      # ★ 自主角色（新增）
│   ├── __init__.py             # 导出 Librarian / Evolver
│   ├── base.py                 # AutonomousRole 抽象基类（LLM 调用 + 洞见持久化）
│   ├── librarian.py            # 知识管理者：对话 → 知识图谱（每日 01:00）
│   └── evolver.py              # 系统进化者：性能分析 → 进化建议（每周一 04:00）
│
├── data_bridge/                # CoEvo 数据桥接层
│   ├── team_context.py         # 团队快照（Redis 缓存 30min）
│   ├── queries.py              # CoEvo 只读查询函数库
│   ├── global_research_monitor.py  # 全球研究热点抓取（arXiv / Semantic Scholar）
│   ├── research_direction_analyzer.py  # 研究方向聚类分析
│   ├── risk_engine.py          # 学生风险评分引擎
│   ├── narrative.py            # 成长叙事生成
│   ├── action_tracker.py       # 待办事项跟踪
│   └── risk_alerts.py          # 风险预警推送
│
├── agent_actions/              # Agent API 底层实现
│   ├── __init__.py             # 导出所有 action 函数
│   ├── overview.py             # get_team_overview / list_all_members
│   ├── context.py              # get_person_context / get_meeting_details
│   ├── analysis.py             # find_best_collaborators / compute_collaboration_score
│   ├── query.py                # execute_coevo_query（只读 SQL）
│   └── risk.py                 # compute_student_risk / generate_growth_narrative
│
├── scheduler/
│   ├── scheduler.py            # APScheduler + 管理 API（PUT/DELETE/POST 动态调度）
│   ├── jobs.py                 # cron 作业实现（含 Librarian / Evolver runner）
│   └── evolution.py            # 邮件摘要 + SMTP 发送
│
├── skills/                     # 技能目录
│   ├── pi_agent/               # ★ 核心 PI 助手技能（工具最丰富）
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       └── teamlab_api.py  # 所有 PI Agent 工具函数（含知识库 / 进化报告）
│   ├── student_progress/
│   ├── collaboration_recommend/
│   ├── individual_guidance/
│   ├── team_survey/
│   ├── direction_discovery/
│   ├── literature_review/
│   ├── academic_writing/
│   ├── meeting_record/
│   ├── research_trend/
│   ├── email_digest/
│   └── feishu_interaction/
│
├── web/
│   ├── index.html              # Vue 3 + TailwindCSS + ECharts（含调度管理 + 知识图谱 Tab）
│   └── static/js/
│       ├── app.js              # Vue 应用（含队列 UX / 进度条 / 知识图谱管理）
│       └── particles.js        # 粒子背景动画
│
├── data/
│   ├── migrations/
│   │   ├── 001_init.sql        # 初始 Schema（业务表）
│   │   ├── 002_pi_agent_insights.sql  # AI 洞见表
│   │   └── 003_knowledge_graph.sql    # 知识图谱分层记忆表
│   ├── seeds/
│   │   └── 001_defaults.sql    # 默认能力维度 + PI 配置
│   ├── logs/                   # 运行时日志（不提交）
│   └── pids/                   # 进程 PID（不提交）
│
├── claw/                       # OpenClaw Docker 配置
│   ├── docker-compose.yml      # OpenClaw 单独部署编排
│   ├── docker-compose.full.yml # 全容器（OpenClaw + TeamLab）编排
│   ├── Makefile                # 快捷操作命令
│   └── workspace/              # OpenClaw Agent 行为配置（AGENTS.md 等）
│
└── docs/
    ├── architecture.md         # 系统架构（含知识检索流程）
    ├── database.md             # 数据库 Schema（含知识图谱表）
    ├── deployment.md           # 部署手册（Docker + 本地）
    ├── development.md          # 本文件
    ├── api-reference.md        # API 接口文档
    ├── skills.md               # 技能开发指南
    └── openclaw-integration.md # OpenClaw ↔ TeamLab 集成说明
```

## Local Development Setup

```bash
# Create and activate environment
conda create -n claw_teamlab python=3.12 -y
conda activate claw_teamlab
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
# Edit .env with your local settings

# Initialize database
python main.py init-db

# Start in development mode (with hot reload)
# Set DEBUG=true in .env to enable uvicorn auto-reload
python main.py web
```

## Adding a New API Endpoint

1. Create or edit a route file in `gateway/routes/`
2. Define your endpoint with FastAPI decorators
3. Use `config.database.get_db()` for database access:

```python
from config.database import get_db

@router.get("/api/example")
async def get_example():
    async with get_db() as session:
        result = await session.execute(select(MyModel))
        return result.scalars().all()
```

4. Include new routers in `gateway/routes/api.py`

## Adding a New Skill

1. Create directory: `skills/my_skill/`
2. Write `SKILL.md` following the template (see docs/skills.md)
3. Optionally add `scripts/` with Python helper functions
4. Add intent routing keywords in `config/agents.yaml`:

```yaml
intent_routing:
  rules:
    - skill: my_skill
      patterns: ["keyword1", "keyword2"]
```

5. Add card builder in `feishu/cards.py` (if Feishu output needed)
6. Add card routing in `feishu/receiver.py` `_build_card_for_skill()`

## Key Patterns

### Async Database Access

Always use async context manager:

```python
from config.database import get_db
from sqlalchemy import select
from models import Student

async with get_db() as session:
    stmt = select(Student).where(Student.status == "active")
    result = await session.execute(stmt)
    claw_students = result.scalars().all()
```

### Redis Key Prefixing

Always use `rkey()` to prefix Redis keys with the project namespace:

```python
from config.database import get_redis, rkey

r = await get_redis()
await r.set(rkey("cache:student:1"), data, ex=300)  # 5min TTL
```

### LLM Calls

Workers use OpenAI-compatible API:

```python
from openai import OpenAI
from config.settings import settings

client = OpenAI(base_url=settings.LLM_BASE_URL, api_key=settings.LLM_API_KEY or "unused")
resp = client.chat.completions.create(
    model=settings.LLM_MODEL,
    messages=[...],
)
```

### Feishu Card Building

All cards follow the Feishu Interactive Message Card format:

```python
def build_my_card(data: dict) -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": _header("Title", "blue"),
        "elements": [
            _md("**Bold** and normal text"),
            _divider(),
            _md("More content"),
        ],
    }
```

## Testing

```bash
# Verify all Python files parse correctly
python -c "import ast, glob; [ast.parse(open(f).read()) for f in glob.glob('**/*.py', recursive=True)]; print('OK')"

# Test database connection
python main.py init-db

# Test API endpoints
curl http://localhost:10301/api/system/status
curl http://localhost:10301/api/dashboard/overview

# Test worker health
curl http://localhost:10310/health

# Test chat endpoint
curl -X POST http://localhost:10301/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "hello", "user_id": "test"}'
```

## Code Conventions

- **Python**: 3.12+, type hints, async/await for I/O
- **ORM**: SQLAlchemy 2.0 async style (`select()`, not `query()`)
- **API**: FastAPI with Pydantic models for request/response validation
- **Naming**: snake_case for Python, kebab-case for URLs
- **Logging**: Use `logging.getLogger(__name__)`, structured log messages
- **Error handling**: Let FastAPI handle HTTP errors; log internal errors
