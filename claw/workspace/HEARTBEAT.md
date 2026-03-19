# HEARTBEAT.md — C-Si TeamLab PI 助手自主巡检

每 30 分钟执行一次（08:00–23:00 激活时段）。
**有异常才推送，无异常静默 HEARTBEAT_OK。**

---

## 巡检步骤

### Step 1：一次调用获取所有监控数据（< 500ms，纯 DB，无 LLM）

```bash
MONITOR=$(curl -sf --max-time 10 "http://127.0.0.1:10301/api/agent/monitor" 2>/dev/null)
```

解析结果：
```bash
RED_RISKS=$(echo "$MONITOR" | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('red_risks',[])), d.get('red_risks',[]))")
STALE=$(echo "$MONITOR" | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('stale_actions',[])), d.get('stale_actions',[]))")
RESEARCH=$(echo "$MONITOR" | python3 -c "import json,sys; d=json.load(sys.stdin); r=d.get('global_research_today'); print(r['summary'] if r else '')" 2>/dev/null)
```

### Step 2：决策树

```
if RED_RISKS > 0:
    → 推送风险告警（见模板 A）
elif STALE > 0:
    → 推送逾期行动项提醒（见模板 B）
elif RESEARCH 非空（仅工作日早 9 点前的心跳）:
    → 推送研究动态（见模板 C）
else:
    → 回复 HEARTBEAT_OK（静默）
```

---

## 告警模板

### 模板 A — 红色风险预警
```
⚠️ 团队风险预警

[姓名] 出现红色风险信号：
[content 摘要，不超过2句话]

💡 建议立即安排 1-on-1，发送「给我看看[姓名]的详细档案」获取完整分析。
```

### 模板 B — 逾期行动项提醒
```
📋 有 [N] 项行动项已超过 7 天未更新：

[每项：标题 — owner — 截止日期]

💡 发送「帮我跟进这些行动项」让 PI Agent 生成跟进建议。
```

### 模板 C — 全球研究动态推送（仅有相关成果时）
```
📡 今日全球研究动态

[summary，2-3句话，聚焦与团队相关的部分]

💡 发送「展开分析这个研究方向」获取团队影响分析。
```

---

## 记忆积累

每次巡检结束（无论是否告警），追加到 `memory/YYYY-MM-DD.md`：
```
## [HH:MM] Heartbeat
- 红色风险：[N个] | 逾期行动项：[N个] | 研究动态：[有/无]
- 告警：[NONE 或简短说明]
```

---

## 硬性规则

- ✅ 后端不可达时：**静默跳过**，回复 `HEARTBEAT_OK`
- ✅ 无异常时：**只回复 `HEARTBEAT_OK`**，不发任何消息
- ❌ 禁止发送"系统运行正常"类无意义消息
- ❌ 禁止连续两次发送相同告警（检查 memory 是否已报过）
