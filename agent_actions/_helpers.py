"""
Agent Actions — 公共辅助工具，供各子模块复用。
"""
import logging
from typing import Any

logger = logging.getLogger("agent_actions.helpers")


async def suggest_users(name: str, db_session, limit: int = 5) -> list[str]:
    """
    返回与 name 相似的成员名称列表，用于 NOT FOUND 时给用户提示候选。

    策略：
    1. 前 2 字相同（最可能是形近字/同音字场景）
    2. 首字相同（同姓）
    3. 任意 1 字命中（兜底）
    每步结果去重后合并，最多返回 limit 条。
    """
    from sqlalchemy import text

    seen: set[str] = set()
    results: list[str] = []

    async def _query_names(like_pattern: str) -> list[str]:
        r = await db_session.execute(
            text(
                "SELECT username FROM users "
                "WHERE username LIKE :n AND is_active=1 ORDER BY id LIMIT :lim"
            ),
            {"n": like_pattern, "lim": limit * 2},
        )
        return [row[0] for row in r.fetchall()]

    # 前 2 字相同
    if len(name) >= 2:
        for uname in await _query_names(f"%{name[:2]}%"):
            if uname not in seen:
                seen.add(uname)
                results.append(uname)

    # 仅首字相同（同姓）
    if len(name) >= 1 and len(results) < limit:
        for uname in await _query_names(f"{name[0]}%"):
            if uname not in seen:
                seen.add(uname)
                results.append(uname)

    # 任意 1 字命中（逐字检测，取 name 中每个汉字）
    if len(results) < limit:
        for ch in name:
            for uname in await _query_names(f"%{ch}%"):
                if uname not in seen:
                    seen.add(uname)
                    results.append(uname)
            if len(results) >= limit:
                break

    return results[:limit]


async def resolve_user(name: str, db_session) -> tuple[dict[str, Any] | None, str | None]:
    """
    从 coevo_db 中按姓名模糊解析用户。

    策略（依次降级）：
    1. username LIKE '%{name}%'  — 精确含字匹配
    2. username LIKE '%{name[:2]}%' — 前 2 字模糊（处理形近字，如 甄园谊↔甄园昌）
    3. project_members.display_name LIKE '%{name}%' — 按项目显示名匹配
    4. 未找到时：返回 NOT_FOUND 结构，附带候选名单，供上层立即询问用户

    Returns:
        (user_dict, note_or_None) — note 非空时说明使用了降级匹配
        (None, error_msg)         — 完全未找到，error_msg 中含候选列表
    """
    from sqlalchemy import text

    # Step 1: 精确 username LIKE 匹配
    r = await db_session.execute(
        text(
            "SELECT id, username, bio, role FROM users "
            "WHERE username LIKE :n AND is_active=1 ORDER BY id LIMIT 3"
        ),
        {"n": f"%{name}%"},
    )
    users = r.mappings().all()
    if users:
        exact = [u for u in users if u["username"] == name]
        chosen = dict(exact[0] if exact else users[0])
        note = None if chosen["username"] == name else f"（已按最近匹配定位到 '{chosen['username']}'）"
        return chosen, note

    # Step 2: 前 2 字模糊（针对形近字替换情形）
    if len(name) >= 2:
        prefix = name[:2]
        r = await db_session.execute(
            text(
                "SELECT id, username, bio, role FROM users "
                "WHERE username LIKE :n AND is_active=1 ORDER BY id LIMIT 3"
            ),
            {"n": f"%{prefix}%"},
        )
        users = r.mappings().all()
        if users:
            chosen = dict(users[0])
            note = f"⚠️ 未找到 '{name}'，已自动匹配最近似成员 '{chosen['username']}'（前两字相同）"
            logger.info("resolve_user: fuzzy matched '%s' → '%s'", name, chosen["username"])
            return chosen, note

    # Step 3: project_members.display_name
    r = await db_session.execute(
        text(
            "SELECT u.id, u.username, u.bio, u.role "
            "FROM project_members pm "
            "JOIN users u ON u.id = pm.user_id AND u.is_active = 1 "
            "WHERE pm.display_name LIKE :n LIMIT 1"
        ),
        {"n": f"%{name}%"},
    )
    row = r.mappings().first()
    if row:
        chosen = dict(row)
        note = f"（通过项目显示名匹配到 '{chosen['username']}'）"
        return chosen, note

    # Step 4: 完全未找到 — 查询候选名单，生成友好提示
    candidates = await suggest_users(name, db_session)
    if candidates:
        candidate_str = "、".join(f"'{c}'" for c in candidates)
        hint = (
            f"[NOT_FOUND] 系统中未找到成员 '{name}'。\n"
            f"您是否想查询：{candidate_str}？\n"
            "请让用户确认正确的名字后重试。"
        )
    else:
        hint = (
            f"[NOT_FOUND] 系统中未找到成员 '{name}'，"
            "且无相似候选。请调用 GET /api/agent/members 查看完整成员列表。"
        )

    logger.warning("resolve_user: '%s' not found, candidates=%s", name, candidates)
    return None, hint
