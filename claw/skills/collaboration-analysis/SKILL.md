---
name: collaboration-analysis
description: 深度分析团队成员间的合作潜力，为项目组建团队或协作配对提供数据驱动的推荐。当 PI 询问"谁跟谁合作最好"、"为某项目组建团队"、"推荐合作者"时使用。触发词：合作推荐、谁适合合作、组建团队、最佳搭档、协作分析。
---

# 协作推荐深度分析

适用场景：PI 需要为新项目组建团队，或评估某两人合作的可行性。

## ⚠️ 名字验证（第一步，必须执行）

**在任何分析之前**，先确认成员存在：

```bash
SEARCH=$(curl -s "http://127.0.0.1:10301/api/agent/search-member?q={name}")
FOUND=$(echo "$SEARCH" | python3 -c "import sys,json;print(json.load(sys.stdin)['found'])")
```

- 若 `found=true`：继续执行下方流程
- 若 `found=false`：**立即停止，将 `candidates` 展示给用户，询问确认**，不做任何分析

```
抱歉，未找到"{name}"。您是否想查询：<candidates>？请确认后我再为您分析。
```

## 场景 A：为某人推荐最佳合作者

1. （已在名字验证中确认成员存在）
2. `curl -s "http://127.0.0.1:10301/api/agent/best-collaborators?name={name}&top=5"` — 推荐最佳合作者
   - **若返回 HTTP 404**：停止，展示候选给用户确认（见上方容错规则）
3. 对 Top 2-3 候选人：`curl -s "http://127.0.0.1:10301/api/agent/collaboration-score?person_a={name}&person_b={candidate}"` 深化分析
4. 考虑维度：能力互补性、目标协同度、blockers 互解可能、当前工作负载
5. `curl -s -X POST "http://127.0.0.1:10301/api/agent/save-collaboration" -H "Content-Type: application/json" -d '{"person_a":"A","person_b":"B","score":85,"reasoning":"...","ideas":[]}'` 持久化推荐结果

## 场景 B：为新项目组建团队

1. `curl -s "http://127.0.0.1:10301/api/agent/members?role=student"` — 列出所有候选人
2. 对每个候选人：`curl -s "http://127.0.0.1:10301/api/agent/person-context?name={name}"` 评估可用性和方向匹配度
3. 对重点候选组合：`curl -s "http://127.0.0.1:10301/api/agent/collaboration-score?person_a=A&person_b=B"` 两两评估
4. 筛选出 1-2 个最优组合
5. `curl -s -X POST "http://127.0.0.1:10301/api/agent/save-collaboration" ...` 记录结果

## 输出结构

```
## 合作推荐结果

### 推荐组合
| 排名 | 合作者 | 合作价值分 | 推荐理由 |
|------|--------|-----------|---------|
（引用具体数据：能力互补点、blockers 互解场景）

### 注意事项
（工作负载冲突 / 研究方向可能的分歧 / 时间线建议）

### 不推荐的组合
（如有明显不适合的原因，也要说明）
```

## 注意

- 推荐理由必须引用具体数据，不要给空泛的"能力互补"
- 考虑当前工作负载，避免推荐已超负荷的学生
- 完成后始终调用 `/api/agent/save-collaboration` 持久化
