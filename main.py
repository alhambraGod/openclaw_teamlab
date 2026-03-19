"""
OpenClaw TeamLab — CLI Entry Point
Unified launcher for gateway, scheduler, workers, and utility commands.
"""
import argparse
import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

from config.settings import settings
from config.log_setup import setup_logging

# ── Directories ──
PID_DIR = settings.PID_DIR
LOG_DIR = settings.LOG_DIR


def _ensure_dirs():
    """Create data/logs and data/pids directories if they don't exist."""
    PID_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _write_pid(name: str, pid: int):
    """Write a PID file."""
    _ensure_dirs()
    (PID_DIR / f"{name}.pid").write_text(str(pid))


def _read_pid(name: str) -> int | None:
    """Read a PID file, return None if missing or stale."""
    path = PID_DIR / f"{name}.pid"
    if not path.exists():
        return None
    try:
        pid = int(path.read_text().strip())
        # Check if process is still alive
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        path.unlink(missing_ok=True)
        return None


def _remove_pid(name: str):
    """Remove a PID file."""
    (PID_DIR / f"{name}.pid").unlink(missing_ok=True)


# ── Subcommands ──

def cmd_web(args):
    """Start the FastAPI gateway."""
    logger = setup_logging("web")
    port = args.port or settings.PORT

    logger.info("Starting gateway on port %d", port)
    _write_pid("gateway", os.getpid())

    try:
        import uvicorn
        uvicorn.run(
            "gateway.app:app",
            host=settings.HOST,
            port=port,
            log_level="info",
            reload=settings.DEBUG,
        )
    finally:
        _remove_pid("gateway")


def cmd_scheduler(args):
    """Start the APScheduler process."""
    logger = setup_logging("scheduler")
    logger.info("Starting scheduler on port %d", settings.SCHEDULER_PORT)
    _write_pid("scheduler", os.getpid())

    try:
        from scheduler.scheduler import run_scheduler
        run_scheduler()
    finally:
        _remove_pid("scheduler")


def cmd_workers(args):
    """Start the worker pool."""
    logger = setup_logging("workers")
    count = args.count or settings.WORKER_MIN

    logger.info("Starting %d workers", count)
    _write_pid("workers", os.getpid())

    try:
        from workers.pool import start_pool, stop_pool
        start_pool(count=count)

        # Keep the manager process alive until interrupted
        logger.info("Worker pool running. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Stopping worker pool...")
            stop_pool()
    finally:
        _remove_pid("workers")


def cmd_all(args):
    """Start gateway, scheduler, and workers as subprocesses."""
    logger = setup_logging("main")
    _ensure_dirs()

    project_root = str(settings.PROJECT_ROOT)
    python = sys.executable
    procs: dict[str, subprocess.Popen] = {}
    log_handles: dict[str, object] = {}
    _stop_event = False
    _shutting_down = False          # 防止信号处理器重入
    _restart_fails: dict[str, int] = {}  # 记录各组件连续失败次数（用于退避）

    def spawn(name: str, cmd: list[str]) -> subprocess.Popen:
        log_file = LOG_DIR / f"teamlab_{name}.log"
        fh = open(log_file, "a")
        log_handles[name] = fh
        proc = subprocess.Popen(
            cmd,
            cwd=project_root,
            stdout=fh,
            stderr=subprocess.STDOUT,
        )
        _write_pid(name, proc.pid)
        procs[name] = proc
        logger.info("Started %s (pid=%d)", name, proc.pid)
        return proc

    def shutdown():
        nonlocal _stop_event, _shutting_down
        if _shutting_down:
            return
        _shutting_down = True
        _stop_event = True
        logger.info("Shutting down all components...")
        for name, proc in list(procs.items()):
            try:
                proc.terminate()
                proc.wait(timeout=10)
                logger.info("Stopped %s (pid=%d)", name, proc.pid)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                logger.warning("Force-killed %s (pid=%d)", name, proc.pid)
            except Exception as exc:
                logger.error("Error stopping %s: %s", name, exc)
            _remove_pid(name)
        for fh in log_handles.values():
            try:
                fh.close()
            except Exception:
                pass

    def _sig_handler(signum, frame):
        shutdown()
        sys.exit(0)

    # 先注册信号，再启动子进程
    signal.signal(signal.SIGTERM, _sig_handler)
    signal.signal(signal.SIGINT, _sig_handler)

    # Spawn all components
    spawn("gateway", [python, "main.py", "web"])
    time.sleep(1)  # Let gateway start before scheduler connects
    spawn("scheduler", [python, "main.py", "scheduler"])
    spawn("workers", [python, "main.py", "workers"])

    logger.info("All components started. Press Ctrl+C to stop.")

    _component_cmds = {
        "gateway":   [python, "main.py", "web"],
        "scheduler": [python, "main.py", "scheduler"],
        "workers":   [python, "main.py", "workers"],
    }

    try:
        while not _stop_event:
            for name, proc in list(procs.items()):
                ret = proc.poll()
                if ret is not None and not _stop_event:
                    fails = _restart_fails.get(name, 0) + 1
                    _restart_fails[name] = fails
                    # 指数退避：连续失败时等待更长（上限 30s），防止端口冲突时狂刷重启
                    backoff = min(30, 2 ** min(fails - 1, 4))
                    if ret != 0:
                        logger.warning(
                            "%s exited with code %d (fail #%d) — wait %ds then restart",
                            name, ret, fails, backoff,
                        )
                    else:
                        logger.info(
                            "%s exited cleanly (fail_count reset) — restarting after %ds",
                            name, backoff,
                        )
                        _restart_fails[name] = 0
                    _remove_pid(name)
                    time.sleep(backoff)
                    if not _stop_event:
                        spawn(name, _component_cmds[name])
                        if ret == 0:
                            _restart_fails[name] = 0
                elif ret is None:
                    # 组件正常运行，重置失败计数
                    _restart_fails.pop(name, None)
            time.sleep(2)
    except (KeyboardInterrupt, SystemExit):
        shutdown()
        sys.exit(0)


def cmd_status(args):
    """Query gateway for system status."""
    _ensure_dirs()
    gateway_url = f"http://localhost:{settings.PORT}"

    # Show PID status
    print("=== OpenClaw TeamLab — System Status ===\n")

    for component in ["gateway", "scheduler", "workers"]:
        pid = _read_pid(component)
        status = f"running (pid={pid})" if pid else "stopped"
        print(f"  {component:12s} {status}")

    print()

    # Query gateway API
    try:
        resp = httpx.get(f"{gateway_url}/api/system/status", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            print(f"  Gateway:         {data.get('gateway', 'unknown')}")
            print(f"  Uptime:          {data.get('uptime_seconds', 0)}s")
            print(f"  Active workers:  {data.get('active_workers', 0)}")
            print(f"  Queue length:    {data.get('queue_length', 'N/A')}")
            print(f"  Redis:           {data.get('redis', 'unknown')}")
        else:
            print(f"  Gateway returned HTTP {resp.status_code}")
    except Exception as exc:
        print(f"  Gateway unreachable: {exc}")

    # Query scheduler
    try:
        resp = httpx.get(
            f"http://localhost:{settings.SCHEDULER_PORT}/health", timeout=5
        )
        if resp.status_code == 200:
            data = resp.json()
            print(f"\n  Scheduler:       {data.get('status', 'unknown')}")
            print(f"  Scheduler jobs:  {data.get('job_count', 0)}")
    except Exception:
        print("\n  Scheduler:       unreachable")

    print()


def cmd_stop(args):
    """Stop all running components by reading PID files and sending SIGTERM."""
    logger = setup_logging("stop")
    _ensure_dirs()

    stopped = 0
    for component in ["workers", "scheduler", "gateway"]:
        pid = _read_pid(component)
        if pid is None:
            print(f"  {component:12s} not running")
            continue

        try:
            os.kill(pid, signal.SIGTERM)
            print(f"  {component:12s} stopped (pid={pid})")
            logger.info("Sent SIGTERM to %s (pid=%d)", component, pid)
            stopped += 1
        except ProcessLookupError:
            print(f"  {component:12s} already dead (pid={pid})")
        except PermissionError:
            print(f"  {component:12s} permission denied (pid={pid})")

        _remove_pid(component)

    if stopped == 0:
        print("  No running components found.")
    else:
        print(f"\n  Stopped {stopped} component(s).")


def cmd_init_db(args):
    """Create database (if needed) and run table creation."""
    logger = setup_logging("init_db")
    logger.info("Initializing database...")

    async def _init():
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy import text
        from config.database import engine, close_db
        from models import Base

        # Step 1: Create the database itself (connect without specifying a DB)
        no_db_dsn = (
            f"mysql+aiomysql://{settings.MYSQL_USER}:{settings.MYSQL_PASSWORD}"
            f"@{settings.MYSQL_HOST}:{settings.MYSQL_PORT}/"
            f"?charset=utf8mb4"
        )
        tmp_engine = create_async_engine(no_db_dsn, echo=False)
        async with tmp_engine.begin() as conn:
            await conn.execute(text(
                f"CREATE DATABASE IF NOT EXISTS `{settings.MYSQL_DATABASE}` "
                f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            ))
        await tmp_engine.dispose()
        logger.info("Database '%s' created/verified", settings.MYSQL_DATABASE)

        # Step 2: Create all tables
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("All tables created/verified")

        # Step 3: Run seed data if available
        seed_file = settings.PROJECT_ROOT / "data" / "seeds" / "001_defaults.sql"
        if seed_file.exists():
            seed_sql = seed_file.read_text(encoding="utf-8")
            async with engine.begin() as conn:
                for statement in seed_sql.split(";"):
                    stmt = statement.strip()
                    if stmt and not stmt.startswith("--"):
                        try:
                            await conn.execute(text(stmt))
                        except Exception as exc:
                            # Ignore duplicate key errors from re-running seeds
                            if "Duplicate" not in str(exc):
                                logger.warning("Seed statement failed: %s", exc)
            logger.info("Seed data loaded")

        await close_db()

    asyncio.run(_init())
    print("Database initialized successfully.")


def cmd_chat(args):
    """Send a chat message via CLI."""
    message = args.message
    if not message:
        print("Error: message is required")
        sys.exit(1)

    gateway_url = f"http://localhost:{settings.PORT}"

    try:
        resp = httpx.post(
            f"{gateway_url}/api/chat",
            json={"message": message, "user_id": "cli"},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            task_id = data.get("task_id")
            print(f"Task submitted: {task_id}")
            print("Polling for result...")

            # Poll for result
            for _ in range(60):
                time.sleep(2)
                try:
                    result_resp = httpx.get(
                        f"{gateway_url}/api/chat/result/{task_id}", timeout=10
                    )
                    if result_resp.status_code == 200:
                        result = result_resp.json()
                        status = result.get("status")
                        if status == "completed":
                            print(f"\n{result.get('result_summary', 'No summary')}")
                            return
                        elif status == "failed":
                            print(f"\nTask failed: {result.get('error_message')}")
                            return
                        # Still running/queued — keep polling
                except Exception:
                    pass

            print("\nTimeout: task did not complete within 120 seconds.")
        else:
            print(f"Error: HTTP {resp.status_code} — {resp.text}")
    except Exception as exc:
        print(f"Error: {exc}")
        sys.exit(1)


# ── Main ──

def main():
    parser = argparse.ArgumentParser(
        prog="openclaw_teamlab",
        description="OpenClaw TeamLab — AI Research Team Management Platform",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # web
    p_web = subparsers.add_parser("web", help="Start the gateway server")
    p_web.add_argument("--port", type=int, default=None, help="Port (default: from settings)")

    # start (alias used by ~/.openclaw/manager.sh)
    subparsers.add_parser("start", help="Start the scheduler (manager.sh compatible)")

    # scheduler (alias for start)
    subparsers.add_parser("scheduler", help="Start the scheduler")

    # workers
    p_workers = subparsers.add_parser("workers", help="Start the worker pool")
    p_workers.add_argument("--count", "-n", type=int, default=None, help="Number of workers")

    # all
    subparsers.add_parser("all", help="Start all components")

    # status
    subparsers.add_parser("status", help="Show system status")

    # stop
    subparsers.add_parser("stop", help="Stop all components")

    # init-db
    subparsers.add_parser("init-db", help="Initialize database tables")

    # chat
    p_chat = subparsers.add_parser("chat", help="Send a chat message via CLI")
    p_chat.add_argument("message", type=str, help="The message to send")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    commands = {
        "web": cmd_web,
        "start": cmd_scheduler,       # manager.sh calls "start" for scheduler
        "scheduler": cmd_scheduler,
        "workers": cmd_workers,
        "all": cmd_all,
        "status": cmd_status,
        "stop": cmd_stop,
        "init-db": cmd_init_db,
        "chat": cmd_chat,
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
