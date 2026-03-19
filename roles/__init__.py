"""
OpenClaw TeamLab — Autonomous Roles

每个角色代表一个独立的自主智能体，定期运行、积累知识、推动系统自我进化。

角色列表：
  Librarian  — 知识管理者：从对话中提取团队知识，形成持久记忆
  Evolver    — 系统进化者：分析系统性能，发现改进点，推动自我迭代
  Researcher — 研究侦察者：封装 global_research_monitor，已由调度器驱动
  RiskGuard  — 风险守护者：封装 risk_engine，已由调度器驱动
  Compass    — 方向罗盘者：封装 research_direction_analyzer，已由调度器驱动
"""
from roles.librarian import Librarian
from roles.evolver import Evolver

__all__ = ["Librarian", "Evolver"]
