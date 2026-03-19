---
name: student-profile
description: 生成学生深度档案，全面了解某位学生的研究状态、成长轨迹、风险评估和发展建议。当 PI 需要准备指导谈话、评估学生进展、或深入了解某人时使用。触发词：[学生姓名] 怎么样、[姓名] 最近进展、给我看看 [姓名]、学生档案。
---

# 学生深度档案

适用场景：PI 需要了解某学生的全貌，准备指导谈话，或评估某学生的综合状态。

## ⚠️ 第 0 步：先验证成员是否存在

```bash
SEARCH=$(curl -s "http://127.0.0.1:10301/api/agent/search-member?q={name}")
FOUND=$(echo "$SEARCH" | python3 -c "import sys,json;print(json.load(sys.stdin)['found'])")
```

若 `found=false`：**立即回复用户，展示候选名单，等待确认后再继续**。不得继续调用下方步骤。

## 执行步骤（成员已确认存在后执行）

1. `curl -s "http://127.0.0.1:10301/api/agent/person-context?name={name}"` — 获取完整信息（项目目标、blockers、会议记录、能力评分）
2. `curl -s "http://127.0.0.1:10301/api/agent/risk?student_name={name}"` — 当前风险评估（进度风险、能力短板、情绪信号）
3. `curl -s "http://127.0.0.1:10301/api/agent/growth-narrative?name={name}&months=3"` — 近三个月成长叙事（纵向对比）
4. `curl -s "http://127.0.0.1:10301/api/agent/best-collaborators?name={name}&top=3"` — 最适合此学生的合作者（备用）
5. 使用 `curl -s -X POST "http://127.0.0.1:10301/api/agent/query" -H "Content-Type: application/json" -d '{"sql":"..."}'` 或团队分析接口获取研究方向，再搜索 arXiv

## 输出结构

```
## [姓名] — 当前状态（📊 风险等级：红/黄/绿）

### 研究现状
（项目目标完成度 / 当前阻塞 / 关键里程碑）

### 成长轨迹（近3个月）
（能力变化 / 突破点 / 停滞点）

### 风险信号
（具体列出：什么风险，依据什么数据，建议如何应对）

### 合作建议
（如当前有需要协作的 blocker，推荐最合适的队友）

### 推荐阅读
（与其研究方向相关的最新论文，附一句话摘要）

### PI 行动建议
（下次 1-on-1 重点讨论的 1-2 个问题）
```

## 重要提示

- 不要在群聊或公开频道展示学生的具体分数和导师评语
- 如发现红色风险信号，立即主动告知 PI，不要等 PI 问
- 完成后用 `curl -s -X POST "http://127.0.0.1:10301/api/agent/log-insight" -H "Content-Type: application/json" -d '{"insight_type":"other","content":"...","subject":"..."}'` 保存关键发现
