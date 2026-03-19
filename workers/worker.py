"""
OpenClaw TeamLab — Worker Process
A standalone FastAPI micro-server that executes skills via LLM calls.
Run as: python -m workers.worker --port PORT --worker-id WORKER_ID --gateway-url URL
"""
import argparse
import asyncio
import json
import logging
import os
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import httpx
import openai
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import text

from config.settings import settings
from config.database import get_redis, get_db, rkey
from config.log_setup import setup_logging, TaskLogger
from workers.skill_loader import load_skill
from workers.tool_executor import execute_tool, serialize_tool_result, MAX_TOOL_ITERATIONS

logger = logging.getLogger("teamlab.worker")

# ── CLI Arguments (populated at module level by __main__ block) ──
worker_id: str = "worker-0"
gateway_url: str = f"http://localhost:{settings.PORT}"
worker_port: int = settings.WORKER_PORT_BASE

# 每个 worker 进程允许同时运行的最大并发 LLM 任务数
WORKER_CONCURRENCY: int = int(os.environ.get("WORKER_CONCURRENCY", "5"))

# 任务最大执行时间（秒）；超时后自动取消并通知用户
TASK_TIMEOUT_SECONDS: int = int(os.environ.get("TASK_TIMEOUT_SECONDS", "180"))  # 默认 3 分钟

# ── Concurrency State ──
_start_time: float = 0.0
_task_semaphore: asyncio.Semaphore  # initialized in lifespan
_active_task_count: int = 0         # active concurrent task counter
_active_tasks: dict[str, str] = {}  # task_id -> skill


async def _emit_progress(task_id: str, step: str, detail: str = "", percent: int = 0):
    """发布任务进度事件到 Redis Pub/Sub，由 Gateway 转发到 WebSocket 客户端。

    进度事件为尽力传递，失败不影响主流程。
    """
    try:
        r = await get_redis()
        event = {
            "task_id": task_id,
            "step": step,
            "detail": detail,
            "percent": percent,
            "worker_id": worker_id,
            "ts": time.time(),
        }
        await r.publish(rkey("task:progress"), json.dumps(event, ensure_ascii=False))
    except Exception:
        pass  # 进度事件是 best-effort，不影响主流程


async def _notify_timeout(
    task_id: str,
    user_id: str,
    source: str,
    input_text: str,
    duration_ms: int,
    callback_url: Optional[str] = None,
    email: Optional[str] = None,
):
    """
    超时发生时三通道并发通知：
    1. Redis Pub/Sub → WebSocket → 页面实时弹窗
    2. Gateway 内部回调 → 更新 MySQL + WebSocket 定向推送
    3. callback_url → OpenClaw (Feishu/CLI 回复)

    所有通知均为尽力传递，不阻塞主流程。
    """
    timeout_msg = (
        f"⏳ 您好！这个问题的分析超过了 {TASK_TIMEOUT_SECONDS // 60} 分钟，系统已自动回收资源。\n\n"
        f"您的问题：「{input_text[:80]}{'…' if len(input_text) > 80 else ''}」\n\n"
        "💡 建议：\n"
        "• 稍后重试（已有缓存时会快很多）\n"
        "• 将问题拆解，如先查成员信息再查合作推荐\n"
        "• 通过 POST /api/chat 提交为后台异步任务，完成后自动通知"
    )

    # 1. Redis Pub/Sub（通过 Gateway 转发到 WebSocket 客户端）
    try:
        r = await get_redis()
        event = {
            "task_id": task_id,
            "step": "timeout",
            "detail": timeout_msg,
            "percent": 0,
            "worker_id": worker_id,
            "ts": time.time(),
        }
        await r.publish(rkey("task:progress"), json.dumps(event, ensure_ascii=False))
    except Exception as e:
        logger.debug("Timeout pubsub notify failed: %s", e)

    # 2. Gateway 内部回调（更新 MySQL + WebSocket 定向推送）
    try:
        async with httpx.AsyncClient(timeout=8) as http:
            await http.post(
                f"{gateway_url}/api/internal/task-complete",
                json={
                    "task_id": task_id,
                    "worker_id": worker_id,
                    "status": "timeout",
                    "result_summary": timeout_msg,
                    "duration_ms": duration_ms,
                    "error_message": f"Task timed out after {TASK_TIMEOUT_SECONDS}s",
                },
            )
    except Exception as e:
        logger.debug("Timeout gateway callback failed: %s", e)

    # 3. callback_url（OpenClaw 接收后发送飞书/CLI 回复）
    if callback_url:
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                await http.post(
                    callback_url,
                    json={
                        "task_id": task_id,
                        "status": "timeout",
                        "result_summary": timeout_msg,
                        "error_message": f"Task timed out after {TASK_TIMEOUT_SECONDS}s",
                        "duration_ms": duration_ms,
                        "source": source,
                        "user_id": user_id,
                    },
                )
            logger.info("Timeout callback sent to %s for task %s", callback_url, task_id)
        except Exception as e:
            logger.warning("Timeout callback_url POST failed (%s): %s", callback_url, e)

    # 4. 邮件通知（用户明确指定邮箱时）
    if email:
        try:
            from notify.email import send_task_timeout_email
            await send_task_timeout_email(email, task_id, input_text)
            logger.info("Timeout email sent to %s for task %s", email, task_id)
        except Exception as e:
            logger.warning("Timeout email failed (%s): %s", email, e)


# ── Pydantic Models ──
class TaskPayload(BaseModel):
    task_id: str
    user_id: str
    source: str
    skill: str
    input_text: str
    callback_url: Optional[str] = None  # 完成后 POST 结果，供 OpenClaw 等异步回传
    email: Optional[str] = None         # 用户指定的邮箱，完成/超时时发送结果邮件


# ── Lifespan ──
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _start_time, _task_semaphore
    _start_time = time.time()
    _task_semaphore = asyncio.Semaphore(WORKER_CONCURRENCY)
    await _register_worker()
    logger.info(
        "Worker %s started on port %s (concurrency=%d)",
        worker_id, worker_port, WORKER_CONCURRENCY,
    )
    hb_task = asyncio.create_task(_heartbeat())
    yield
    hb_task.cancel()
    await _deregister_worker()
    logger.info("Worker %s shut down", worker_id)


app = FastAPI(title="OpenClaw Worker", lifespan=lifespan)


# ── Redis Registration ──
def _worker_info(status: str, task_id: str | None = None) -> str:
    """Build Redis worker registration payload."""
    slots_available = _task_semaphore._value if hasattr(_task_semaphore, "_value") else WORKER_CONCURRENCY
    return json.dumps({
        "worker_id": worker_id,
        "status": status,  # "idle" = has capacity | "busy" = full
        "port": worker_port,
        "url": f"http://127.0.0.1:{worker_port}",
        "active_tasks": _active_task_count,
        "max_tasks": WORKER_CONCURRENCY,
        "slots_available": max(0, WORKER_CONCURRENCY - _active_task_count),
        "task_id": task_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


async def _register_worker():
    r = await get_redis()
    await r.hset(rkey("workers"), worker_id, _worker_info("idle"))
    await r.set(rkey(f"worker:alive:{worker_id}"), "1", ex=45)


async def _deregister_worker():
    try:
        r = await get_redis()
        await r.hdel(rkey("workers"), worker_id)
        await r.delete(rkey(f"worker:alive:{worker_id}"))
    except Exception:
        logger.warning("Failed to deregister worker %s from Redis", worker_id)


async def _set_redis_status(status: str, task_id: str | None = None):
    r = await get_redis()
    await r.hset(rkey("workers"), worker_id, _worker_info(status, task_id))
    await r.set(rkey(f"worker:alive:{worker_id}"), "1", ex=45)


async def _heartbeat():
    """Periodically refresh Redis registration with live slot/task counts."""
    try:
        while True:
            await asyncio.sleep(15)
            try:
                # busy = semaphore exhausted (no free slots)
                current_status = "busy" if _active_task_count >= WORKER_CONCURRENCY else "idle"
                await _set_redis_status(current_status)
            except Exception as exc:
                logger.warning("Heartbeat refresh failed: %s", exc)
    except asyncio.CancelledError:
        pass


# ── Endpoints ──
@app.get("/health")
async def health():
    uptime = round(time.time() - _start_time, 2)
    return {"status": "healthy", "worker_id": worker_id, "uptime": uptime}


@app.get("/status")
async def status():
    slots_free = WORKER_CONCURRENCY - _active_task_count
    return {
        "status": "busy" if slots_free <= 0 else "idle",
        "worker_id": worker_id,
        "active_tasks": _active_task_count,
        "max_tasks": WORKER_CONCURRENCY,
        "slots_available": max(0, slots_free),
        "active_task_ids": list(_active_tasks.keys()),
    }


@app.post("/task")
async def accept_task(payload: TaskPayload):
    """
    接受并异步处理任务。
    asyncio 单线程模型保证 check-then-increment 的原子性（无 await 在两者之间）。
    容量满时立即返回 409，由 Dispatcher 将任务放回 Redis 队列。
    """
    global _active_task_count, _active_tasks
    if _active_task_count >= WORKER_CONCURRENCY:
        return {
            "error": "Worker at capacity",
            "worker_id": worker_id,
            "active": _active_task_count,
            "max": WORKER_CONCURRENCY,
        }, 409

    _active_task_count += 1
    _active_tasks[payload.task_id] = payload.skill
    # Update Redis immediately so pool.get_idle_worker sees fresh slot count
    asyncio.create_task(_set_redis_status(
        "busy" if _active_task_count >= WORKER_CONCURRENCY else "idle",
        payload.task_id,
    ))
    asyncio.create_task(_process_task(payload))
    return {
        "accepted": True,
        "task_id": payload.task_id,
        "worker_id": worker_id,
        "slots_remaining": WORKER_CONCURRENCY - _active_task_count,
    }


# ── Task Processing ──
async def _run_task_inner(
    payload: TaskPayload,
    tlog: "TaskLogger",  # type: ignore[name-defined]
) -> tuple[str, Optional[str], Optional[dict]]:
    """
    核心 LLM + 工具执行逻辑。
    返回 (task_status, result_summary, result_data)。
    可被 asyncio.wait_for 在超时时取消。
    """
    result_summary = None
    result_data = None
    task_status = "completed"

    try:
        # 1. Mark status in Redis
        await _set_redis_status("busy", payload.task_id)
        await _emit_progress(payload.task_id, "started", f"开始处理（{payload.skill}）", 5)

        # 2. Load the skill
        skill = load_skill(payload.skill)
        await _emit_progress(payload.task_id, "skill_loaded", f"技能已加载: {payload.skill}", 10)

        # 3. Build messages for LLM
        messages = [{"role": "system", "content": skill["system_prompt"]}]

        # Append reference context if available
        if skill["references"]:
            ref_block = "\n\n---\nReference Materials:\n" + "\n\n".join(skill["references"])
            messages[0]["content"] += ref_block

        # Inject live team context so the LLM can answer factual questions
        # about real members, projects, and collaborations from CoEvo DB.
        try:
            from data_bridge.team_context import get_team_snapshot
            await _emit_progress(payload.task_id, "context_loading", "正在加载团队上下文...", 15)
            team_ctx = await get_team_snapshot()
            if team_ctx:
                messages[0]["content"] += (
                    "\n\n---\n"
                    "## 当前团队真实数据（来自 cognalign-coevo 数据库）\n"
                    "请优先基于以下数据回答用户问题，不要凭空编造成员信息或假设数据。\n\n"
                    + team_ctx
                )
                tlog.context_injected(len(team_ctx))
                await _emit_progress(payload.task_id, "context_loaded", "团队上下文已注入", 20)
        except Exception as _ctx_err:
            logger.warning("Failed to inject team context: %s", _ctx_err)

        # 知识图谱语义检索注入（L2/L3，混合检索策略）
        # 优先使用 KnowledgeRetriever（向量 + 图谱 + 档案）
        # 降级回 claw_pi_agent_insights 平铺查询
        try:
            from knowledge.retriever import KnowledgeRetriever
            await _emit_progress(payload.task_id, "knowledge_retrieving", "检索知识库...", 17)
            kr = KnowledgeRetriever()
            knowledge_ctx = await kr.retrieve_for_query(
                query=payload.input_text,
                session_entities=None,
                k=8,
            )
            if knowledge_ctx:
                messages[0]["content"] += (
                    "\n\n---\n"
                    + knowledge_ctx
                )
                tlog.context_injected(len(knowledge_ctx))
                await _emit_progress(payload.task_id, "knowledge_loaded", "知识库检索完成", 20)
            else:
                # 无知识图谱数据时退化到平铺查询
                raise ValueError("empty knowledge graph, fallback")
        except Exception as _kb_err:
            logger.debug("KnowledgeRetriever failed (%s), falling back to claw_pi_agent_insights", _kb_err)
            try:
                from sqlalchemy import text as sa_text
                async with get_db() as db:
                    rows = (await db.execute(
                        sa_text("""
                            SELECT subject, content FROM claw_pi_agent_insights
                            WHERE insight_type = 'team_knowledge'
                              AND created_at >= DATE_SUB(NOW(), INTERVAL 14 DAY)
                            ORDER BY created_at DESC LIMIT 20
                        """)
                    )).mappings().all()
                    if rows:
                        knowledge_block = "\n".join(
                            f"- **{r['subject']}**: {r['content']}" for r in rows
                        )
                        messages[0]["content"] += (
                            "\n\n---\n"
                            "## 知识管理者积累的团队知识（近14天）\n"
                            + knowledge_block
                        )
            except Exception as _fb_err:
                logger.debug("Fallback knowledge injection failed: %s", _fb_err)

        messages.append({"role": "user", "content": payload.input_text})

        # 记录用户原始输入
        tlog.user_input(payload.input_text, user_id=payload.user_id, source=payload.source)

        # 4. Build tool descriptions from skill scripts
        tools = None
        if skill["tools"]:
            tools = []
            for tool_desc in skill["tools"]:
                tools.append({
                    "type": "function",
                    "function": {
                        "name": tool_desc["name"],
                        "description": tool_desc["description"],
                        "parameters": tool_desc.get("parameters", {"type": "object", "properties": {}}),
                    },
                })

        # 5. Call LLM
        client = openai.AsyncOpenAI(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY or "no-key",
        )

        kwargs = {
            "model": settings.LLM_MODEL,
            "messages": messages,
            "max_tokens": settings.LLM_MAX_TOKENS,
        }
        if tools:
            kwargs["tools"] = tools

        # 记录 LLM 请求
        tlog.llm_request(
            model=settings.LLM_MODEL,
            message_count=len(messages),
            has_tools=tools is not None,
        )
        await _emit_progress(payload.task_id, "llm_thinking", "AI 正在思考...", 25)

        response = await client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        # 记录第一次 LLM 回复
        usage_dict = {}
        if response.usage:
            usage_dict = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
            }
        if choice.message.content:
            tlog.llm_response(
                content=choice.message.content,
                usage=usage_dict,
                iteration=0,
            )

        # ── Tool execution loop ──
        iteration = 0
        while choice.message.tool_calls and iteration < MAX_TOOL_ITERATIONS:
            iteration += 1
            logger.info(
                "Task %s: executing %d tool call(s) (iteration %d/%d)",
                payload.task_id,
                len(choice.message.tool_calls),
                iteration,
                MAX_TOOL_ITERATIONS,
            )

            # Append the assistant message with tool_calls
            messages.append(choice.message)

            # Execute each tool and collect results
            for tc in choice.message.tool_calls:
                func_name = tc.function.name
                try:
                    func_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    func_args = {}

                # 记录工具调用
                tlog.tool_call(func_name, func_args, iteration=iteration)
                # 进度：每个工具调用占 10%，最多到 80%
                tool_pct = min(80, 30 + iteration * 15)
                await _emit_progress(
                    payload.task_id, "tool_call",
                    f"调用工具: {func_name}",
                    tool_pct,
                )

                tool_result = await execute_tool(skill["name"], func_name, func_args)
                result_str = serialize_tool_result(tool_result)

                # 记录工具返回结果
                tlog.tool_result(func_name, result_str, iteration=iteration)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })

                logger.debug(
                    "Tool %s returned %d chars", func_name, len(result_str)
                )

            # Re-call LLM with tool results
            kwargs["messages"] = messages
            response = await client.chat.completions.create(**kwargs)
            choice = response.choices[0]

            # 记录每次 tool 循环后的 LLM 回复
            iter_usage = {}
            if response.usage:
                iter_usage = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                }
            if choice.message.content:
                tlog.llm_response(
                    content=choice.message.content,
                    usage=iter_usage,
                    iteration=iteration,
                )

        await _emit_progress(payload.task_id, "synthesizing", "正在整理回答...", 88)
        result_summary = choice.message.content or ""
        result_data = {
            "model": response.model,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
            },
            "tool_calls_executed": iteration,
        }

    except Exception as exc:
        logger.error("Task %s failed: %s", payload.task_id, exc, exc_info=True)
        task_status = "failed"
        error_message = f"{type(exc).__name__}: {exc}"
        result_summary = None
        result_data = None
        await _emit_progress(payload.task_id, "failed", f"处理失败: {error_message[:80]}", 0)
        return task_status, result_summary, result_data

    await _emit_progress(payload.task_id, "completed", "处理完成", 100)
    return task_status, result_summary, result_data


async def _process_task(payload: TaskPayload):
    """
    外层管理器：超时控制 + 并发槽管理 + 结果持久化 + 多通道通知。

    执行流：
      _run_task_inner  ──wait_for(5min)──►  正常完成 → MySQL + gateway + callback_url
                                       ├──► TimeoutError → _notify_timeout（三通道）
                                       └──► 其他异常 → failed 状态
    """
    global _active_task_count, _active_tasks
    start_ms = time.time()
    tlog = TaskLogger(task_id=payload.task_id, skill=payload.skill, worker_id=worker_id)
    task_status = "completed"
    result_summary = None
    result_data = None
    error_message = None

    try:
        task_status, result_summary, result_data = await asyncio.wait_for(
            _run_task_inner(payload, tlog),
            timeout=TASK_TIMEOUT_SECONDS,
        )

    except asyncio.TimeoutError:
        duration_ms = int((time.time() - start_ms) * 1000)
        task_status = "timeout"
        error_message = f"Task timed out after {TASK_TIMEOUT_SECONDS}s"
        logger.warning(
            "Task %s timed out after %ds (skill=%s, user=%s)",
            payload.task_id, TASK_TIMEOUT_SECONDS, payload.skill, payload.user_id,
        )
        tlog.task_complete(duration_ms=duration_ms, status="timeout", error=error_message)

        # MySQL: 标记超时
        try:
            async with get_db() as session:
                await session.execute(
                    text(
                        """
                        INSERT INTO claw_task_log
                            (task_id, user_id, source, skill_used, input_text,
                             status, worker_id, duration_ms, error_message,
                             timeout_at, callback_url)
                        VALUES
                            (:task_id, :user_id, :source, :skill_used, :input_text,
                             'timeout', :worker_id, :duration_ms, :error_message,
                             NOW(), :callback_url)
                        ON DUPLICATE KEY UPDATE
                            status = 'timeout',
                            worker_id = VALUES(worker_id),
                            duration_ms = VALUES(duration_ms),
                            error_message = VALUES(error_message),
                            timeout_at = NOW()
                        """
                    ),
                    {
                        "task_id": payload.task_id,
                        "user_id": payload.user_id,
                        "source": payload.source,
                        "skill_used": payload.skill,
                        "input_text": payload.input_text,
                        "worker_id": worker_id,
                        "duration_ms": duration_ms,
                        "error_message": error_message,
                        "callback_url": getattr(payload, "callback_url", None),
                    },
                )
        except Exception as db_exc:
            logger.error("Failed to write timeout to MySQL: %s", db_exc)

        # 三通道通知
        await _notify_timeout(
            task_id=payload.task_id,
            user_id=payload.user_id,
            source=payload.source,
            input_text=payload.input_text,
            duration_ms=duration_ms,
            callback_url=getattr(payload, "callback_url", None),
            email=getattr(payload, "email", None),
        )

    except Exception as exc:
        duration_ms = int((time.time() - start_ms) * 1000)
        task_status = "failed"
        error_message = f"{type(exc).__name__}: {exc}"
        logger.error("Task %s outer failed: %s", payload.task_id, exc)

    finally:
        # 无论任何情况都释放并发槽
        _active_task_count = max(0, _active_task_count - 1)
        _active_tasks.pop(payload.task_id, None)
        new_status = "busy" if _active_task_count >= WORKER_CONCURRENCY else "idle"
        await _set_redis_status(new_status)

    if task_status == "timeout":
        # 超时路径已在上面处理完毕，直接返回
        return

    duration_ms = int((time.time() - start_ms) * 1000)
    tlog.task_complete(duration_ms=duration_ms, status=task_status, error=error_message or "")

    # 更新滚动平均耗时统计（用于 ETA 预测）
    if task_status == "completed":
        try:
            r = await get_redis()
            await r.lpush(rkey("stats:durations"), duration_ms)
            await r.ltrim(rkey("stats:durations"), 0, 199)
            durations_raw = await r.lrange(rkey("stats:durations"), 0, -1)
            if durations_raw:
                avg = int(sum(int(d) for d in durations_raw) / len(durations_raw))
                await r.set(rkey("stats:avg_duration_ms"), avg)
        except Exception as _stats_err:
            logger.debug("Stats update failed: %s", _stats_err)

    # MySQL: 持久化结果
    try:
        async with get_db() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO claw_task_log
                        (task_id, user_id, source, skill_used, input_text,
                         result_summary, result_data, status, worker_id,
                         duration_ms, error_message, completed_at, callback_url)
                    VALUES
                        (:task_id, :user_id, :source, :skill_used, :input_text,
                         :result_summary, :result_data, :status, :worker_id,
                         :duration_ms, :error_message, NOW(), :callback_url)
                    ON DUPLICATE KEY UPDATE
                        result_summary = VALUES(result_summary),
                        result_data = VALUES(result_data),
                        status = VALUES(status),
                        worker_id = VALUES(worker_id),
                        duration_ms = VALUES(duration_ms),
                        error_message = VALUES(error_message),
                        completed_at = NOW()
                    """
                ),
                {
                    "task_id": payload.task_id,
                    "user_id": payload.user_id,
                    "source": payload.source,
                    "skill_used": payload.skill,
                    "input_text": payload.input_text,
                    "result_summary": result_summary,
                    "result_data": json.dumps(result_data) if result_data else None,
                    "status": task_status,
                    "worker_id": worker_id,
                    "duration_ms": duration_ms,
                    "error_message": error_message,
                    "callback_url": getattr(payload, "callback_url", None),
                },
            )
    except Exception as exc:
        logger.error("Failed to store task result in MySQL: %s", exc, exc_info=True)

    # Gateway 内部回调（WebSocket 推送结果）
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            await http.post(
                f"{gateway_url}/api/internal/task-complete",
                json={
                    "task_id": payload.task_id,
                    "worker_id": worker_id,
                    "status": task_status,
                    "result_summary": result_summary,
                    "duration_ms": duration_ms,
                    "error_message": error_message,
                },
            )
    except Exception as exc:
        logger.warning("Failed to notify gateway of task completion: %s", exc)

    # callback_url：OpenClaw 飞书/CLI 异步回传
    if getattr(payload, "callback_url", None):
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                await http.post(
                    payload.callback_url,
                    json={
                        "task_id": payload.task_id,
                        "status": task_status,
                        "result_summary": result_summary,
                        "result_data": result_data,
                        "error_message": error_message,
                    },
                )
        except Exception as exc:
            logger.warning("Callback to %s failed: %s", payload.callback_url, exc)

    # 邮件通知：用户指定邮箱时，任务完成发送结果
    if getattr(payload, "email", None) and task_status in ("completed", "failed"):
        try:
            from notify.email import send_task_result_email, send_email
            if task_status == "completed":
                await send_task_result_email(
                    payload.email, payload.task_id, payload.input_text,
                    result_summary or "",
                )
            else:
                await send_email(
                    payload.email,
                    f"❌ TeamLab 任务失败：{payload.input_text[:40]}",
                    f"您的任务执行失败。\n\n问题：{payload.input_text}\n\n错误：{error_message or '未知错误'}\n\n任务 ID：{payload.task_id}",
                )
            logger.info("Result email sent to %s for task %s", payload.email, payload.task_id)
        except Exception as exc:
            logger.warning("Result email failed (%s): %s", payload.email, exc)

    logger.info(
        "Task %s %s in %dms (skill=%s, active=%d/%d)",
        payload.task_id, task_status, duration_ms, payload.skill,
        _active_task_count, WORKER_CONCURRENCY,
    )


# ── Main ──
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OpenClaw Worker Process")
    parser.add_argument("--port", type=int, required=True, help="Port to listen on")
    parser.add_argument("--worker-id", type=str, required=True, help="Unique worker identifier")
    parser.add_argument(
        "--gateway-url", type=str,
        default=f"http://localhost:{settings.PORT}",
        help="Gateway base URL for callbacks",
    )
    args = parser.parse_args()

    worker_id = args.worker_id
    worker_port = args.port
    gateway_url = args.gateway_url

    # 使用统一日志模块（写 teamlab_workers.log + teamlab_all.log）
    setup_logging("workers")
    logging.getLogger("teamlab.worker").info(
        "Worker process starting — id=%s port=%d", worker_id, worker_port
    )

    uvicorn.run(app, host="127.0.0.1", port=worker_port, log_level="info")
