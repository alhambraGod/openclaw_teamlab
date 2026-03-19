"""
OpenClaw TeamLab — Skill Loader
Reads skill definitions from the skills/ directory.
Also provides keyword-based intent classification from agents.yaml config.
"""
import ast
import logging
import os
from pathlib import Path

from config.settings import settings

logger = logging.getLogger("teamlab.skill_loader")


def load_skill(skill_name: str) -> dict:
    """
    Load a skill by name from the skills/ directory.

    Returns:
        {
            "name": str,
            "system_prompt": str,
            "tools": [{"name": str, "description": str, "parameters": dict}, ...],
            "references": [str, ...],
        }

    Raises:
        FileNotFoundError: if skill directory or SKILL.md does not exist.
    """
    skill_dir = settings.SKILLS_DIR / skill_name

    if not skill_dir.is_dir():
        raise FileNotFoundError(f"Skill directory not found: {skill_dir}")

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(f"SKILL.md not found for skill '{skill_name}': {skill_md}")

    system_prompt = skill_md.read_text(encoding="utf-8").strip()
    tools = _extract_tools(skill_dir / "scripts")
    references = _load_references(skill_dir / "references")

    logger.debug(
        "Loaded skill '%s': %d tools, %d references",
        skill_name, len(tools), len(references),
    )
    return {
        "name": skill_name,
        "system_prompt": system_prompt,
        "tools": tools,
        "references": references,
    }


def list_skills() -> list[str]:
    """Return a list of all available skill names (directories under skills/)."""
    skills_dir = settings.SKILLS_DIR
    if not skills_dir.is_dir():
        return []
    return sorted(
        d.name
        for d in skills_dir.iterdir()
        if d.is_dir() and not d.name.startswith("_") and not d.name.startswith(".")
    )


def classify_intent_from_config(text: str) -> str:
    """Keyword-based intent classification using agents.yaml routing rules.

    Returns a skill name string. Falls back to the configured default skill.
    Used by the gateway queue consumer when no skill is pre-assigned.
    """
    import yaml

    try:
        config_path = settings.PROJECT_ROOT / "config" / "agents.yaml"
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        routing = config.get("intent_routing", {})
        default_skill = routing.get("default_skill", "individual_guidance")
        rules = routing.get("rules", [])
        text_lower = text.lower()
        for rule in rules:
            for pattern in rule.get("patterns", []):
                if pattern.lower() in text_lower:
                    return rule["skill"]
        return default_skill
    except Exception as exc:
        logger.warning("classify_intent_from_config error: %s", exc)
        return "individual_guidance"


def _extract_tools(scripts_dir: Path) -> list[dict]:
    """
    Extract function signatures and docstrings from Python scripts
    to use as LLM tool descriptions.
    """
    tools = []
    if not scripts_dir.is_dir():
        return tools

    for py_file in sorted(scripts_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError) as exc:
            logger.warning("Failed to parse %s: %s", py_file, exc)
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_"):
                continue

            docstring = ast.get_docstring(node) or f"Function {node.name} from {py_file.name}"

            params = {}
            for arg in node.args.args:
                if arg.arg == "self":
                    continue
                param_type = "string"
                if arg.annotation:
                    try:
                        param_type = ast.unparse(arg.annotation).lower()
                        if "int" in param_type:
                            param_type = "integer"
                        elif "float" in param_type:
                            param_type = "number"
                        elif "bool" in param_type:
                            param_type = "boolean"
                        else:
                            param_type = "string"
                    except Exception:
                        param_type = "string"
                params[arg.arg] = {"type": param_type, "description": f"Parameter: {arg.arg}"}

            tools.append({
                "name": node.name,
                "description": docstring,
                "parameters": {
                    "type": "object",
                    "properties": params,
                },
            })

    return tools


def _load_references(references_dir: Path) -> list[str]:
    """Load all .md files from the references directory."""
    refs = []
    if not references_dir.is_dir():
        return refs

    for md_file in sorted(references_dir.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8").strip()
            if content:
                refs.append(content)
        except (UnicodeDecodeError, OSError) as exc:
            logger.warning("Failed to read reference %s: %s", md_file, exc)

    return refs
