#!/usr/bin/env bash
# =============================================================================
# teamlab.sh — 调用 TeamLab PI 管理后端（LLM Worker 驱动）
#
# 用法：
#   teamlab.sh "任务描述"
#   teamlab.sh "任务描述" --user "feishu:ou_xxx" --channel feishu
#   teamlab.sh "任务描述" --async
#   teamlab.sh "任务描述" --timeout 120
#
# 执行模式：
#   飞书渠道（--channel feishu 且 --user feishu:ou_xxx）→ 自动 fire-and-forget
#     提交任务后立即返回确认，后端异步执行，完成后直接推送飞书消息给用户
#   Web UI / CLI → 同步轮询模式（最多 SYNC_TIMEOUT 秒），超时自动转 fire-and-forget
#
# 环境变量（优先级：外部环境变量 > 自动探测）：
#   TEAMLAB_BASE_URL   后端地址（未设置时自动探测）
#   TEAMLAB_USER       用户标识（默认：openclaw）
#   TEAMLAB_CHANNEL    渠道标识（默认：openclaw_agent）
# =============================================================================

set -euo pipefail

TEAMLAB_USER="${TEAMLAB_USER:-openclaw}"
TEAMLAB_CHANNEL="${TEAMLAB_CHANNEL:-openclaw_agent}"
# 同步等待上限：超时后转 fire-and-forget，避免阻塞 OpenClaw Agent
SYNC_TIMEOUT="${SYNC_TIMEOUT:-90}"
POLL_INTERVAL=3
ASYNC_MODE=false
TASK=""

# ── 参数解析 ──────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)    TEAMLAB_USER="$2";    shift 2 ;;
    --channel) TEAMLAB_CHANNEL="$2"; shift 2 ;;
    --timeout) SYNC_TIMEOUT="$2";    shift 2 ;;
    --async)   ASYNC_MODE=true;      shift   ;;
    *)         TASK="${TASK}${TASK:+ }$1"; shift ;;
  esac
done

if [[ -z "$TASK" ]]; then
  echo "用法: teamlab.sh \"<任务描述>\" [--user USER] [--channel feishu|web|api] [--timeout 秒] [--async]" >&2
  exit 1
fi

# ── 飞书渠道：同步轮询（延长超时），结果在当前对话轮次内直接返回 ───────────────
# 不再使用 fire-and-forget：OpenClaw 无法在 Feishu channel 中补发独立消息，
# 异步模式会导致用户永远收不到结果。改为同步等待，超时后告知任务 ID。
if [[ "$TEAMLAB_CHANNEL" == "feishu" ]] && [[ "$TEAMLAB_USER" == feishu:ou_* ]]; then
  SYNC_TIMEOUT="${SYNC_TIMEOUT:-180}"   # 飞书渠道最长等 3 分钟
fi

# ── 后端地址解析 ──────────────────────────────────────────────────────────────
# 优先使用 TEAMLAB_BASE_URL（docker-compose 已注入）；未设置时按顺序探测
_resolve_teamlab_url() {
  local candidates=(
    "http://claw-teamlab:10301"           # Docker 模式：容器间内网（主）
    "http://127.0.0.1:10301"              # 宿主机直连（备，仅 OpenClaw 非 Docker 时有效）
  )
  for url in "${candidates[@]}"; do
    if curl -sf --max-time 2 "${url}/api/system/status" >/dev/null 2>&1; then
      echo "$url"
      return 0
    fi
  done
  echo "http://127.0.0.1:10301"
}

if [[ -z "${TEAMLAB_BASE_URL:-}" ]]; then
  TEAMLAB_BASE_URL="$(_resolve_teamlab_url)"
  echo "[teamlab] 自动探测后端: ${TEAMLAB_BASE_URL}" >&2
else
  echo "[teamlab] 后端: ${TEAMLAB_BASE_URL}" >&2
fi

# ── 健康检查 ──────────────────────────────────────────────────────────────────
if ! curl -sf --max-time 5 "${TEAMLAB_BASE_URL}/api/system/status" >/dev/null 2>&1; then
  cat >&2 <<EOF
❌ TeamLab 后端不可达: ${TEAMLAB_BASE_URL}

请检查以下之一是否已启动：
  全容器模式：make up-all
  混合模式：make backend start（然后 make claw start）
  状态检查：make health
EOF
  exit 1
fi

# ── 序列化请求 JSON ───────────────────────────────────────────────────────────
TASK_JSON=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$TASK")
USER_JSON=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$TEAMLAB_USER")
SRC_JSON=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$TEAMLAB_CHANNEL")

SUBMIT_BODY=$(python3 -c "
import json, sys
d = {
  'message': sys.argv[1],
  'user_id': sys.argv[2],
  'source':  sys.argv[3],
}
print(json.dumps(d))
" "$TASK" "$TEAMLAB_USER" "$TEAMLAB_CHANNEL")

# ── 提交任务 ──────────────────────────────────────────────────────────────────
SUBMIT_RESP=$(curl -sf -X POST "${TEAMLAB_BASE_URL}/api/chat" \
  -H "Content-Type: application/json" \
  -d "${SUBMIT_BODY}" \
  --max-time 15 2>/dev/null) || {
  echo "❌ 任务提交失败，请检查后端日志：make agent logs" >&2
  exit 1
}

TASK_ID=$(python3 -c "import json,sys; print(json.load(sys.stdin).get('task_id',''))" <<< "$SUBMIT_RESP" 2>/dev/null || echo "")
if [[ -z "$TASK_ID" ]]; then
  echo "❌ 返回格式异常：" >&2
  echo "$SUBMIT_RESP" >&2
  exit 1
fi

echo "[teamlab] 任务已提交 task_id=${TASK_ID}" >&2

# ── Fire-and-Forget（飞书 / --async）────────────────────────────────────────
if [[ "$ASYNC_MODE" == "true" ]]; then
  cat <<EOF
✅ 已收到你的 PI 管理任务，正在后台智能分析中。

完成后将**直接通过飞书推送结果**给你，无需等待。

> 任务编号：\`${TASK_ID}\`
> 预计完成时间：视任务复杂度 30 秒 ~ 3 分钟

如需查询进度，可发送：「查询任务 ${TASK_ID} 的结果」
EOF
  exit 0
fi

# ── 同步轮询（Web UI / CLI）─────────────────────────────────────────────────
echo "[teamlab] 等待结果，最多 ${SYNC_TIMEOUT}s…" >&2

elapsed=0
while [[ $elapsed -lt $SYNC_TIMEOUT ]]; do
  sleep "$POLL_INTERVAL"
  elapsed=$((elapsed + POLL_INTERVAL))

  RESULT=$(curl -sf "${TEAMLAB_BASE_URL}/api/chat/result/${TASK_ID}" \
    --max-time 10 2>/dev/null) || continue

  STATUS=$(python3 -c "import json,sys; print(json.load(sys.stdin).get('status',''))" <<< "$RESULT" 2>/dev/null || echo "")

  case "$STATUS" in
    completed)
      python3 - "$RESULT" <<'PYEOF'
import json, sys
d = json.loads(sys.argv[1])
summary  = d.get("result_summary") or "(无摘要)"
duration = d.get("duration_ms", 0)
task_id  = d.get("task_id", "")
secs     = round(duration / 1000, 1)
print(f"**PI Agent 分析完成** · 耗时 {secs}s · `{task_id}`\n\n{summary}")
PYEOF
      exit 0
      ;;
    failed)
      python3 -c "
import json,sys
d=json.load(sys.stdin)
print('❌ 分析失败:', d.get('error_message','未知错误'))
" <<< "$RESULT"
      exit 1
      ;;
    timeout)
      python3 -c "
import json,sys
d=json.load(sys.stdin)
msg=d.get('result_summary') or '⏳ 任务超时（>3分钟），系统已自动回收资源。\n\n💡 建议：稍后重试（有缓存时会快很多），或拆解问题后重新提交。'
print(msg)
" <<< "$RESULT"
      exit 1
      ;;
    queued|running|"")
      echo "[teamlab] 处理中… (已等待 ${elapsed}s)" >&2
      ;;
    *)
      echo "[teamlab] 未知状态: $STATUS" >&2
      ;;
  esac
done

# SYNC_TIMEOUT 内未完成 → 告知用户任务 ID，让他主动查询
cat <<EOF
⏳ **PI 分析耗时较长**（已等待 ${elapsed}s）

后端仍在运行，任务编号：\`${TASK_ID}\`

请稍后发送「查询任务 ${TASK_ID} 的结果」获取分析报告。
EOF
exit 0
