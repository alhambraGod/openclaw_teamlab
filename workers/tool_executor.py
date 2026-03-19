"""
OpenClaw TeamLab — Tool Executor
Dynamically imports and executes skill script functions called by the LLM.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
from pathlib import Path
from typing import Any

from config.settings import settings

logger = logging.getLogger("teamlab.tool_executor")

# Cache of imported modules: {skill_name: {func_name: callable}}
_module_cache: dict[str, dict[str, Any]] = {}

MAX_TOOL_ITERATIONS = 3


def _load_skill_functions(skill_name: str) -> dict[str, Any]:
    """Import all public functions from skills/{skill_name}/scripts/*.py."""
    if skill_name in _module_cache:
        return _module_cache[skill_name]

    funcs: dict[str, Any] = {}
    scripts_dir = settings.SKILLS_DIR / skill_name / "scripts"
    if not scripts_dir.is_dir():
        logger.warning("No scripts/ dir for skill '%s'", skill_name)
        _module_cache[skill_name] = funcs
        return funcs

    for py_file in sorted(scripts_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue

        module_name = f"skills.{skill_name}.scripts.{py_file.stem}"
        try:
            mod = importlib.import_module(module_name)
            for name in dir(mod):
                if name.startswith("_"):
                    continue
                obj = getattr(mod, name)
                if callable(obj) and (inspect.isfunction(obj) or inspect.iscoroutinefunction(obj)):
                    funcs[name] = obj
        except Exception as exc:
            logger.error("Failed to import %s: %s", module_name, exc)

    logger.debug("Loaded %d functions for skill '%s': %s", len(funcs), skill_name, list(funcs.keys()))
    _module_cache[skill_name] = funcs
    return funcs


async def execute_tool(skill_name: str, func_name: str, arguments: dict) -> Any:
    """Execute a tool function by name from the skill's scripts.

    Handles both sync and async functions. Returns the function result
    serialized as a string (for feeding back to the LLM).
    """
    funcs = _load_skill_functions(skill_name)

    if func_name not in funcs:
        error_msg = f"Function '{func_name}' not found in skill '{skill_name}'. Available: {list(funcs.keys())}"
        logger.warning(error_msg)
        return {"error": error_msg}

    func = funcs[func_name]

    try:
        if inspect.iscoroutinefunction(func):
            result = await func(**arguments)
        else:
            result = await asyncio.to_thread(func, **arguments)
    except Exception as exc:
        logger.error("Tool execution failed: %s.%s(%s) -> %s", skill_name, func_name, arguments, exc, exc_info=True)
        return {"error": f"{type(exc).__name__}: {exc}"}

    return result


def serialize_tool_result(result: Any) -> str:
    """Convert a tool result to a JSON string for feeding back to the LLM."""
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(result)
