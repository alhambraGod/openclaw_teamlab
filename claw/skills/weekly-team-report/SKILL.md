---
name: weekly-team-report
description: 生成全团队每周综合管理报告。当 PI 需要一份涵盖所有学生进展、风险变化、行动项状态、前沿动态的完整周报时使用。触发词：周报、团队报告、本周总结、weekly。
---

# 全团队每周综合报告

适用场景：PI 需要一份全面的团队周报，或每周一 cron 自动触发。

## 执行步骤

1. `curl -s "http://127.0.0.1:10301/api/agent/team-overview"` — 获取当前团队全貌（成员、项目、整体状态）
2. `curl -s "http://127.0.0.1:10301/api/agent/risk"` — 计算全员风险（不传 student_name 即全员）
3. `curl -s "http://127.0.0.1:10301/api/agent/action-items?status=stale,open"` — 行动项状态（超期 / 未关闭）
4. `curl -s "http://127.0.0.1:10301/api/agent/team-analytics"` 及自定义 query 获取前沿相关数据
5. 读取 `memory/` 最近两次报告中记录的关键事项
6. 综合生成报告，固定结构：

```
## 本周亮点
（进展突出的学生 / 完成的里程碑）

## 需要关注
（风险等级变化：新增红/黄预警，已消除的预警）

## 行动项进度
（超期 → 正常 → 已完成，列 owner 和 deadline）

## 前沿动态
（近7天 arXiv/Semantic Scholar 与团队方向相关的 top3 论文）

## 下周建议
（基于当前 blockers 和风险，给 PI 的具体行动建议）
```

7. `curl -s -X POST "http://127.0.0.1:10301/api/agent/log-insight" ...` 保存本次报告摘要到数据库
8. 写入 `memory/YYYY-MM-DD.md` 记录本次分析的关键发现
9. 飞书通知由系统 cron 或 PI 手动触发
