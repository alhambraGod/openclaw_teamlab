"""
Agent Actions — 供 OpenClaw Agent 通过 HTTP API 调用的核心逻辑。
Agent 通过 bash+curl 调用 /api/agent/* 端点。
"""
from agent_actions.query import (
    get_team_overview,
    get_person_context,
    execute_coevo_query,
    get_meeting_details,
    get_team_analytics,
    list_all_members,
)
from agent_actions.analysis import (
    compute_student_risk,
    generate_growth_narrative,
    compute_collaboration_score,
    find_best_collaborators,
    get_action_items,
)
from agent_actions.write import (
    log_insight,
    save_collaboration_recommendation,
)

__all__ = [
    "get_team_overview",
    "get_person_context",
    "execute_coevo_query",
    "get_meeting_details",
    "get_team_analytics",
    "list_all_members",
    "compute_student_risk",
    "generate_growth_narrative",
    "compute_collaboration_score",
    "find_best_collaborators",
    "get_action_items",
    "log_insight",
    "save_collaboration_recommendation",
]
