"""
OpenClaw TeamLab — Worker Pool Manager
Manages worker subprocesses from within the Gateway process.
"""
import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

import httpx

from config.settings import settings
from config.database import get_redis, rkey

logger = logging.getLogger("teamlab.pool")

# ── Internal State ──
_workers: dict[str, dict] = {}  # worker_id -> {pid, port, process, fail_count}
_health_task: asyncio.Task | None = None


def _pid_dir() -> Path:
    """Return the PID directory, creating it if needed."""
    pid_dir = settings.PID_DIR
    pid_dir.mkdir(parents=True, exist_ok=True)
    return pid_dir


def _write_pid(worker_id: str, pid: int):
    path = _pid_dir() / f"{worker_id}.pid"
    path.write_text(str(pid))


def _remove_pid(worker_id: str):
    path = _pid_dir() / f"{worker_id}.pid"
    path.unlink(missing_ok=True)


def _next_port() -> int:
    """Find the next available port starting from WORKER_PORT_BASE."""
    used_ports = {w["port"] for w in _workers.values()}
    port = settings.WORKER_PORT_BASE
    while port in used_ports:
        port += 1
    return port


def _spawn_worker(worker_id: str, port: int, gateway_url: str) -> dict:
    """Spawn a single worker subprocess and return its tracking info."""
    cmd = [
        sys.executable, "-m", "workers.worker",
        "--port", str(port),
        "--worker-id", worker_id,
        "--gateway-url", gateway_url,
    ]
    process = subprocess.Popen(
        cmd,
        cwd=str(settings.PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _write_pid(worker_id, process.pid)
    info = {
        "pid": process.pid,
        "port": port,
        "process": process,
        "fail_count": 0,
        "url": f"http://127.0.0.1:{port}",
    }
    _workers[worker_id] = info
    logger.info("Spawned worker %s (pid=%d, port=%d)", worker_id, process.pid, port)
    return info


def _kill_worker(worker_id: str):
    """Send SIGTERM to a worker and clean up."""
    info = _workers.pop(worker_id, None)
    if info is None:
        return
    proc: subprocess.Popen = info["process"]
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
    except OSError:
        pass
    _remove_pid(worker_id)
    logger.info("Stopped worker %s (pid=%d)", worker_id, info["pid"])


# ── Public API ──

def start_pool(
    count: int | None = None,
    gateway_url: str | None = None,
):
    """Spawn N worker subprocesses."""
    count = count or settings.WORKER_MIN
    gateway_url = gateway_url or f"http://localhost:{settings.PORT}"

    for i in range(count):
        wid = f"worker-{i}"
        if wid in _workers:
            continue
        port = _next_port()
        _spawn_worker(wid, port, gateway_url)

    logger.info("Worker pool started: %d workers", len(_workers))


def stop_pool():
    """Gracefully stop all worker subprocesses."""
    worker_ids = list(_workers.keys())
    for wid in worker_ids:
        _kill_worker(wid)
    logger.info("Worker pool stopped")


async def health_check(interval: float = 10.0):
    """
    Background coroutine that periodically pings each worker's /health endpoint.
    Marks a worker unhealthy after 3 consecutive failures and restarts it.
    """
    while True:
        await asyncio.sleep(interval)
        for wid in list(_workers.keys()):
            info = _workers.get(wid)
            if info is None:
                continue
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(f"{info['url']}/health")
                    if resp.status_code == 200:
                        info["fail_count"] = 0
                        continue
            except Exception:
                pass

            info["fail_count"] = info.get("fail_count", 0) + 1
            logger.warning(
                "Worker %s health check failed (%d/3)", wid, info["fail_count"]
            )
            if info["fail_count"] >= 3:
                logger.error("Worker %s unhealthy — restarting", wid)
                await restart_worker(wid)


def start_health_check_loop():
    """Start the health check as a background asyncio task."""
    global _health_task
    if _health_task is None or _health_task.done():
        _health_task = asyncio.create_task(health_check())
        logger.info("Health check loop started")


def stop_health_check_loop():
    """Cancel the background health check task."""
    global _health_task
    if _health_task and not _health_task.done():
        _health_task.cancel()
        _health_task = None


async def auto_scale():
    """
    Scale the pool up or down based on busy percentage.
    - If busy_pct > 0.8 and pool_size < max: spawn more workers.
    - If busy_pct < 0.2 and pool_size > min: remove idle workers.
    """
    r = await get_redis()
    all_workers = await r.hgetall(rkey("workers"))
    total = len(all_workers)
    if total == 0:
        return

    busy_count = 0
    idle_workers = []
    for wid, raw in all_workers.items():
        try:
            data = json.loads(raw)
            if data.get("status") == "busy":
                busy_count += 1
            else:
                idle_workers.append(wid)
        except (json.JSONDecodeError, TypeError):
            pass

    busy_pct = busy_count / total
    pool_size = len(_workers)

    # Scale up
    if busy_pct > 0.8 and pool_size < settings.WORKER_MAX:
        to_add = min(2, settings.WORKER_MAX - pool_size)
        gateway_url = f"http://localhost:{settings.PORT}"
        for _ in range(to_add):
            idx = pool_size
            wid = f"worker-{idx}"
            while wid in _workers:
                idx += 1
                wid = f"worker-{idx}"
            port = _next_port()
            _spawn_worker(wid, port, gateway_url)
            pool_size += 1
        logger.info("Auto-scaled UP: added %d workers (busy_pct=%.2f)", to_add, busy_pct)

    # Scale down
    elif busy_pct < 0.2 and pool_size > settings.WORKER_MIN:
        to_remove = min(2, pool_size - settings.WORKER_MIN)
        removed = 0
        for wid in idle_workers:
            if removed >= to_remove:
                break
            if wid in _workers and len(_workers) > settings.WORKER_MIN:
                _kill_worker(wid)
                # Also remove from Redis
                await r.hdel(rkey("workers"), wid)
                removed += 1
        if removed:
            logger.info(
                "Auto-scaled DOWN: removed %d workers (busy_pct=%.2f)", removed, busy_pct
            )


def _resolve_worker_url(url: str, port: int | None) -> str:
    """
    在 Docker 容器内运行时，将 worker 注册的 127.0.0.1 地址
    替换为 host.docker.internal，使容器能访问宿主机 worker 进程。
    """
    import os
    base = url or f"http://127.0.0.1:{port}"
    if os.environ.get("TEAMLAB_IN_DOCKER") == "1" or os.path.exists("/.dockerenv"):
        return base.replace("http://127.0.0.1:", "http://host.docker.internal:")
    return base


async def get_idle_worker() -> dict | None:
    """
    Query Redis for an available worker and return its connection info.

    Selection strategy (best-fit load balancing):
    1. Only consider workers with status="idle" (有空闲槽位)
    2. Among those, prefer the one with most slots_available (空闲槽位最多)
       to spread load evenly and avoid hot spots.
    3. Returns None if all workers are at capacity.
    """
    r = await get_redis()
    all_workers = await r.hgetall(rkey("workers"))

    best: dict | None = None
    best_slots = -1

    for wid, raw in all_workers.items():
        try:
            data = json.loads(raw)
            if data.get("status") != "idle":
                continue

            slots = data.get("slots_available", 1)
            if slots <= 0:
                continue

            # Skip stale entries: worker heartbeat key should exist
            alive = await r.exists(rkey(f"worker:alive:{wid}"))
            if not alive:
                continue

            if slots > best_slots:
                best_slots = slots
                raw_url = data.get("url", f"http://127.0.0.1:{data.get('port')}")
                best = {
                    "worker_id": wid,
                    "url": _resolve_worker_url(raw_url, data.get("port")),
                    "port": data.get("port"),
                    "slots_available": slots,
                }
        except (json.JSONDecodeError, TypeError):
            continue

    return best


async def restart_worker(worker_id: str):
    """Kill and respawn a specific worker."""
    info = _workers.get(worker_id)
    port = info["port"] if info else _next_port()
    gateway_url_val = f"http://localhost:{settings.PORT}"

    _kill_worker(worker_id)

    # Clean up Redis entry for the old worker
    try:
        r = await get_redis()
        await r.hdel(rkey("workers"), worker_id)
    except Exception:
        pass

    _spawn_worker(worker_id, port, gateway_url_val)
    logger.info("Restarted worker %s on port %d", worker_id, port)


def get_pool_status() -> list[dict]:
    """Return current pool status for diagnostics."""
    result = []
    for wid, info in _workers.items():
        alive = info["process"].poll() is None
        result.append({
            "worker_id": wid,
            "pid": info["pid"],
            "port": info["port"],
            "alive": alive,
            "fail_count": info.get("fail_count", 0),
        })
    return result
