"""
OpenClaw TeamLab — 统一日志配置模块
==============================================
使用方式：
    在各模块最早的初始化位置调用：
        from config.log_setup import setup_logging
        setup_logging("web")   # 组件名

日志文件布局（全部在 data/logs/）：
    teamlab_web.log       — Gateway (FastAPI) 日志
    teamlab_scheduler.log — Scheduler 日志
    teamlab_workers.log   — Worker 进程日志（含用户输入/模型输出/工具调用）
    teamlab_main.log      — 主进程管理日志（cmd_all）
    teamlab_all.log       — 聚合日志：收集所有模块的 WARNING+ 日志

日志策略：
    - 按天滚动（每天 00:00），历史文件加日期后缀：.log.YYYY-MM-DD
    - 保留 30 天，超期自动删除
    - 文件粒度：DEBUG+（记录完整调试信息）
    - 控制台粒度：INFO+（默认），可通过 LOG_LEVEL 环境变量调整
    - teamlab_all.log 只收集 WARNING+ 以减少噪音
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

from config.settings import settings

# ── 常量 ─────────────────────────────────────────────────────────────
LOG_DIR: Path = settings.LOG_DIR
BACKUP_COUNT: int = 30   # 保留天数

# 日志格式：详细格式用于文件，简洁格式用于控制台
_DETAIL_FMT = (
    "%(asctime)s [%(name)s] %(levelname)s "
    "%(filename)s:%(lineno)d — %(message)s"
)
_CONSOLE_FMT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
_DATE_FMT    = "%Y-%m-%d %H:%M:%S"

# 静默第三方噪音：只在第一次 setup_logging 时执行
_NOISY_LOGGERS = (
    "httpx", "httpcore",
    "openai._base_client", "openai.http_client",
    "urllib3", "urllib3.connectionpool",
    "apscheduler.executors", "apscheduler.scheduler",
    "aiomysql", "sqlalchemy.engine",
    "charset_normalizer",
)

# 全局标记，防止重复初始化
_initialized_components: set[str] = set()


def _make_file_handler(filename: str, level: int = logging.DEBUG) -> logging.handlers.TimedRotatingFileHandler:
    """创建按天滚动的文件 handler，历史文件自动加日期后缀。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / filename
    handler = logging.handlers.TimedRotatingFileHandler(
        filename=str(log_file),
        when="midnight",
        interval=1,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
        utc=False,
        delay=False,
    )
    # 历史文件命名：teamlab_web.log.2026-03-17
    handler.suffix = "%Y-%m-%d"
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_DETAIL_FMT, datefmt=_DATE_FMT))
    return handler


def _make_console_handler(level: int = logging.INFO) -> logging.StreamHandler:
    """创建控制台 handler。"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_CONSOLE_FMT, datefmt=_DATE_FMT))
    return handler


def _get_log_level() -> int:
    """从环境变量读取日志级别，默认 INFO。"""
    return getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)


def setup_logging(component: str) -> logging.Logger:
    """
    初始化指定组件的日志系统。

    参数：
        component: 组件名，决定日志文件名前缀。
                   合法值：web / scheduler / workers / main / init_db / stop 等

    返回：
        该组件的根日志器 logging.Logger("teamlab.<component>")

    副作用：
        - 向根日志器添加 file handler（写 teamlab_<component>.log）
        - 向根日志器添加 file handler（写 teamlab_all.log，WARNING+）
        - 向根日志器添加 console handler（INFO+）
        - 静默噪音第三方日志器
    """
    global _initialized_components
    if component in _initialized_components:
        return logging.getLogger(f"teamlab.{component}")

    _initialized_components.add(component)
    console_level = _get_log_level()

    # ── 根日志器：级别设最低，由各 handler 自行过滤 ───────────────────
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # 清理 basicConfig 默认 handler（避免重复输出）
    for h in list(root.handlers):
        if isinstance(h, logging.StreamHandler) and not isinstance(
            h, (logging.FileHandler, logging.handlers.TimedRotatingFileHandler)
        ):
            root.removeHandler(h)

    # ── Handler 1：组件专属日志文件（DEBUG+，完整粒度）─────────────────
    component_filename = f"teamlab_{component}.log"
    component_handler = _make_file_handler(component_filename, logging.DEBUG)
    root.addHandler(component_handler)

    # ── Handler 2：聚合日志文件（WARNING+，收集所有模块异常）──────────
    # 避免同一 all.log handler 被多次添加
    all_log_filename = "teamlab_all.log"
    all_log_path = str(LOG_DIR / all_log_filename)
    if not any(
        isinstance(h, logging.handlers.TimedRotatingFileHandler)
        and getattr(h, "baseFilename", "") == all_log_path
        for h in root.handlers
    ):
        all_handler = _make_file_handler(all_log_filename, logging.WARNING)
        root.addHandler(all_handler)

    # ── Handler 3：控制台（INFO+，默认）──────────────────────────────
    root.addHandler(_make_console_handler(console_level))

    # ── 静默噪音日志 ──────────────────────────────────────────────────
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    logger = logging.getLogger(f"teamlab.{component}")
    logger.info(
        "Logging initialized — component=%s level=%s dir=%s rotate=daily keep=%dd",
        component, settings.LOG_LEVEL, LOG_DIR, BACKUP_COUNT,
    )
    return logger


def get_logger(name: str) -> logging.Logger:
    """快捷方式：获取 teamlab 命名空间下的日志器。
    
    示例：
        logger = get_logger("gateway.queue")
        logger = get_logger("worker.llm")
    """
    return logging.getLogger(f"teamlab.{name}")


# ── 专用日志器工厂（用于记录关键业务事件）────────────────────────────

class TaskLogger:
    """
    任务全生命周期日志器。
    记录：用户输入 → 任务分发 → LLM 调用 → 工具执行 → 最终输出
    
    用法：
        tlog = TaskLogger(task_id="abc123", skill="collaboration_recommend")
        tlog.user_input("张旭华跟谁合作价值最高")
        tlog.llm_request(model="gemini-...", messages=[...])
        tlog.tool_call("find_best_collaborators", {"name": "张旭华"})
        tlog.tool_result("find_best_collaborators", result_str)
        tlog.llm_response(content="...", usage={...})
        tlog.task_complete(duration_ms=1234, status="completed")
    """

    def __init__(self, task_id: str, skill: str, worker_id: str = ""):
        self.task_id = task_id
        self.skill = skill
        self.worker_id = worker_id
        self._log = logging.getLogger(f"teamlab.worker.task")

    def _prefix(self) -> str:
        return f"[task={self.task_id} skill={self.skill} worker={self.worker_id}]"

    def user_input(self, text: str, user_id: str = "", source: str = "") -> None:
        """记录用户输入原文。"""
        self._log.info(
            "%s USER_INPUT user=%s source=%s | %s",
            self._prefix(), user_id, source,
            _truncate(text, 2000),
        )

    def context_injected(self, context_chars: int) -> None:
        """记录注入的团队上下文大小。"""
        self._log.debug(
            "%s CONTEXT_INJECTED chars=%d", self._prefix(), context_chars
        )

    def llm_request(self, model: str, message_count: int, has_tools: bool) -> None:
        """记录 LLM 请求参数（不记录完整 messages 避免日志过大）。"""
        self._log.info(
            "%s LLM_REQUEST model=%s messages=%d has_tools=%s",
            self._prefix(), model, message_count, has_tools,
        )

    def tool_call(self, tool_name: str, args: dict, iteration: int = 0) -> None:
        """记录工具调用。"""
        args_str = str(args)[:500]
        self._log.info(
            "%s TOOL_CALL [iter=%d] %s(%s)",
            self._prefix(), iteration, tool_name, args_str,
        )

    def tool_result(self, tool_name: str, result: str, iteration: int = 0) -> None:
        """记录工具返回结果（截断到 1000 字符）。"""
        self._log.debug(
            "%s TOOL_RESULT [iter=%d] %s → %s",
            self._prefix(), iteration, tool_name, _truncate(result, 1000),
        )

    def llm_response(self, content: str, usage: dict, iteration: int = 0) -> None:
        """记录 LLM 回复内容和 token 用量。"""
        self._log.info(
            "%s LLM_RESPONSE [iter=%d] tokens=%s | %s",
            self._prefix(), iteration,
            f"in={usage.get('prompt_tokens',0)} out={usage.get('completion_tokens',0)}",
            _truncate(content, 2000),
        )

    def task_complete(self, duration_ms: int, status: str, error: str = "") -> None:
        """记录任务完成状态。"""
        if status == "completed":
            self._log.info(
                "%s TASK_DONE status=%s duration=%dms",
                self._prefix(), status, duration_ms,
            )
        else:
            self._log.error(
                "%s TASK_DONE status=%s duration=%dms error=%s",
                self._prefix(), status, duration_ms, error,
            )


class QueueLogger:
    """
    队列调度过程日志器。
    记录：入队 → 分发 → 重试 → 完成
    """

    def __init__(self):
        self._log = logging.getLogger("teamlab.gateway.queue")

    def enqueue(self, task_id: str, skill: str, user_id: str, source: str) -> None:
        self._log.info(
            "ENQUEUE task=%s skill=%s user=%s source=%s",
            task_id, skill, user_id, source,
        )

    def dispatch(self, task_id: str, worker_id: str, skill: str) -> None:
        self._log.info(
            "DISPATCH task=%s → worker=%s skill=%s",
            task_id, worker_id, skill,
        )

    def requeue(self, task_id: str, reason: str) -> None:
        self._log.warning(
            "REQUEUE task=%s reason=%s (no idle worker, will retry)",
            task_id, reason,
        )

    def complete(self, task_id: str, worker_id: str, duration_ms: int, status: str) -> None:
        self._log.info(
            "COMPLETE task=%s worker=%s status=%s duration=%dms",
            task_id, worker_id, status, duration_ms,
        )

    def intent_classified(self, task_id: str, input_text: str, skill: str) -> None:
        self._log.debug(
            "INTENT task=%s skill=%s input=%s",
            task_id, skill, _truncate(input_text, 200),
        )


class SchedulerLogger:
    """
    Scheduler 任务调度日志器。
    """

    def __init__(self):
        self._log = logging.getLogger("teamlab.scheduler")

    def job_trigger(self, job_id: str, schedule: str) -> None:
        self._log.info("JOB_TRIGGER job=%s schedule=%s", job_id, schedule)

    def job_start(self, job_id: str) -> None:
        self._log.info("JOB_START job=%s", job_id)

    def job_complete(self, job_id: str, duration_ms: int) -> None:
        self._log.info("JOB_DONE job=%s duration=%dms", job_id, duration_ms)

    def job_error(self, job_id: str, error: str) -> None:
        self._log.error("JOB_ERROR job=%s error=%s", job_id, error)

    def skill_dispatched(self, job_id: str, skill: str, task_id: str) -> None:
        self._log.info(
            "JOB_SKILL_DISPATCHED job=%s skill=%s task=%s",
            job_id, skill, task_id,
        )


# ── 工具函数 ─────────────────────────────────────────────────────────

def _truncate(text: str, max_len: int) -> str:
    """截断日志文本，避免单条日志过大。"""
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"…[{len(text)-max_len} chars truncated]"
