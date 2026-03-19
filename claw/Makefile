# ============================================================
# C-Si TeamLab — Makefile
#
# 两种运行模式：
#   【全容器】  make up-all        → OpenClaw + Agent 均在 Docker
#   【混合模式】make claw start    → OpenClaw 在 Docker
#              make backend start → Agent 在宿主机
#
# 子命令语法：make <服务> <动作>
#   make claw    start | stop | restart | status | logs
#   make agent   start | stop | restart | status | logs
#   make backend start | stop | restart | status | logs
# ============================================================

# ─── 变量 ────────────────────────────────────────────────────────────────────

# docker-compose.yml：仅 OpenClaw，Agent 在宿主机（host.docker.internal:10301）
COMPOSE_OC   := docker compose -f docker-compose.yml

# docker-compose.full.yml：OpenClaw + Agent 均在 Docker
COMPOSE_FULL := docker compose -f docker-compose.full.yml

OPENCLAW_IMAGE ?= ghcr.io/openclaw/openclaw:latest
TEAMLAB_DIR    := /Users/antonio/openclaws/openclaw_teamlab

BOLD   := \033[1m
RESET  := \033[0m
GREEN  := \033[32m
YELLOW := \033[33m
RED    := \033[31m

# ─── 子命令分发 ───────────────────────────────────────────────────────────────
# 用法：make <服务> <动作>，如 make claw start
# SUBCMD 捕获命令行第二个单词

SUBCMD := $(word 2,$(MAKECMDGOALS))

# 吸收子命令词，防止 make 报"无规则"错误
# 注意：stop/status/logs 在后面有独立 target 定义，这里只吸收 start/restart
.PHONY: start restart
start restart:
	@:

# ─── 默认：帮助 ───────────────────────────────────────────────────────────────

.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo ""
	@echo "$(BOLD)C-Si TeamLab — 命令速览$(RESET)"
	@echo ""
	@echo "$(BOLD)全栈操作$(RESET)"
	@echo "  $(GREEN)make up-all$(RESET)               全部启动（OpenClaw + Agent 均在 Docker）"
	@echo "  $(GREEN)make stop$(RESET)                 停止所有服务（容器 + 宿主机进程）"
	@echo "  $(GREEN)make health$(RESET)               检查所有端口是否在线"
	@echo "  $(GREEN)make logs$(RESET)                 所有日志（OpenClaw 容器 + Agent 文件）"
	@echo ""
	@echo "$(BOLD)OpenClaw 管理$(RESET)  claw-openclaw 容器（单独启动时连接宿主机 Agent）"
	@echo "  $(GREEN)make claw start$(RESET)           启动"
	@echo "  $(GREEN)make claw stop$(RESET)            停止"
	@echo "  $(GREEN)make claw restart$(RESET)         重启"
	@echo "  $(GREEN)make claw status$(RESET)          查看容器状态"
	@echo "  $(GREEN)make claw logs$(RESET)            查看容器日志"
	@echo ""
	@echo "$(BOLD)Agent 管理$(RESET)    claw-teamlab 容器（Docker 模式）"
	@echo "  $(GREEN)make agent start$(RESET)          构建镜像并启动"
	@echo "  $(GREEN)make agent stop$(RESET)           停止容器"
	@echo "  $(GREEN)make agent restart$(RESET)        重启容器"
	@echo "  $(GREEN)make agent status$(RESET)         查看容器状态"
	@echo "  $(GREEN)make agent logs$(RESET)           查看日志（data/logs/）"
	@echo ""
	@echo "$(BOLD)Backend 管理$(RESET)  宿主机进程（开发/调试用）"
	@echo "  $(GREEN)make backend start$(RESET)        后台启动宿主机 Agent"
	@echo "  $(GREEN)make backend stop$(RESET)         停止宿主机 Agent"
	@echo "  $(GREEN)make backend restart$(RESET)      重启宿主机 Agent"
	@echo "  $(GREEN)make backend status$(RESET)       查看进程状态"
	@echo "  $(GREEN)make backend logs$(RESET)         查看日志（同 agent logs）"
	@echo ""
	@echo "$(BOLD)飞书与配对$(RESET)"
	@echo "  $(GREEN)make feishu-pair CODE=xxx$(RESET) 批准飞书配对码"
	@echo "  $(GREEN)make approve-device$(RESET)       批准 Control UI 配对"
	@echo "  $(GREEN)make reset-sessions$(RESET)       清除会话缓存"
	@echo ""
	@echo "$(BOLD)维护$(RESET)"
	@echo "  $(GREEN)make pull$(RESET)                 更新 OpenClaw 镜像"
	@echo "  $(GREEN)make clean-logs$(RESET)           清理 30 天前历史日志"
	@echo "  $(GREEN)make shell-oc$(RESET)             进入 OpenClaw 容器"
	@echo ""
	@echo "  端口: OpenClaw :10300  |  TeamLab :10301  |  Scheduler :10302"
	@echo ""

# ─── 全栈启动 ─────────────────────────────────────────────────────────────────

.PHONY: up-all
up-all: _check-docker _check-feishu
	@echo "$(BOLD)>> [0/4] 停止旧服务、释放端口...$(RESET)"
	@$(COMPOSE_FULL) down 2>/dev/null || true
	@$(COMPOSE_OC) down 2>/dev/null || true
	@$(MAKE) --no-print-directory _kill-host-ports
	@echo "$(BOLD)>> [1/4] 构建 Agent 镜像（claw-teamlab）...$(RESET)"
	@$(COMPOSE_FULL) build claw-teamlab
	@echo "$(BOLD)>> [2/4] 拉取 OpenClaw 最新镜像...$(RESET)"
	@OPENCLAW_IMAGE=$(OPENCLAW_IMAGE) $(COMPOSE_FULL) pull claw-openclaw 2>/dev/null || true
	@echo "$(BOLD)>> [3/4] 启动全部容器（等待 healthy）...$(RESET)"
	@$(COMPOSE_FULL) up -d --remove-orphans --wait || $(MAKE) --no-print-directory _wait-full
	@echo "$(BOLD)>> [4/4] 验证服务...$(RESET)"
	@echo ""
	@echo "$(GREEN)$(BOLD)✓ 全部启动完成$(RESET)"
	@echo "  OpenClaw:   http://localhost:10300"
	@echo "  TeamLab:    http://localhost:10301"
	@echo "  查看状态:   make health"
	@echo "  查看日志:   make logs"

# （_up-all-fresh 已合并到 up-all）

# ─── 停止全部 ─────────────────────────────────────────────────────────────────
# 注意：stop/status/logs 用作子命令（如 make backend stop）时必须是 no-op，
# 只有单独执行（make stop）时才运行实际逻辑。
# 通过检测 firstword(MAKECMDGOALS) 是否等于当前 target 来区分。

.PHONY: stop
stop:
	@if [ "$(firstword $(MAKECMDGOALS))" != "stop" ]; then :; else \
		echo "$(BOLD)>> 停止所有服务...$(RESET)"; \
		if docker info &>/dev/null 2>&1; then \
			$(COMPOSE_FULL) down 2>/dev/null || true; \
			$(COMPOSE_OC) down 2>/dev/null || true; \
		else \
			echo "$(YELLOW)  Docker 未运行，跳过容器停止$(RESET)"; \
		fi; \
		cd $(TEAMLAB_DIR) && conda run -n claw_teamlab python main.py stop 2>/dev/null || true; \
		pkill -f "workers.worker" 2>/dev/null || true; \
		echo "$(GREEN)✓ 全部已停止$(RESET)"; \
	fi

# ─── make claw <action> ───────────────────────────────────────────────────────

.PHONY: claw
claw:
	@$(MAKE) --no-print-directory _claw_$(or $(SUBCMD),_help)

.PHONY: _claw_start _claw_stop _claw_restart _claw_status _claw_logs _claw__help
_claw__help:
	@echo "用法：make claw {start|stop|restart|status|logs}"

_claw_start: _check-docker _check-feishu
	@if [ -n "$$(docker ps -q --filter 'name=claw-openclaw')" ]; then \
		echo "$(BOLD)>> OpenClaw 已运行，重启...$(RESET)"; \
		docker restart claw-openclaw; \
		echo "$(GREEN)✓ OpenClaw 已重启：http://localhost:10300$(RESET)"; \
	else \
		echo "$(BOLD)>> 拉取 OpenClaw 最新镜像...$(RESET)"; \
		OPENCLAW_IMAGE=$(OPENCLAW_IMAGE) $(COMPOSE_OC) pull claw-openclaw; \
		echo "$(BOLD)>> 启动 OpenClaw（等待 healthy）...$(RESET)"; \
		$(COMPOSE_OC) up -d --remove-orphans --wait || $(MAKE) --no-print-directory _wait-oc; \
		echo "$(GREEN)✓ OpenClaw 已启动：http://localhost:10300$(RESET)"; \
		echo "$(YELLOW)  提示：Agent 需已在运行（make backend start 或 make agent start）$(RESET)"; \
	fi

_claw_stop:
	@$(COMPOSE_OC) down
	@echo "$(GREEN)✓ OpenClaw 已停止$(RESET)"

_claw_restart:
	@echo "$(BOLD)>> 重建 claw-openclaw（确保 entrypoint/volumes 等配置变更生效）...$(RESET)"
	@$(COMPOSE_FULL) up -d --force-recreate --no-deps claw-openclaw 2>/dev/null \
		|| $(COMPOSE_OC) up -d --force-recreate --no-deps claw-openclaw 2>/dev/null \
		|| docker restart claw-openclaw
	@echo "  等待 claw-openclaw 健康..."
	@i=0; while [ $$i -lt 30 ] && ! docker inspect --format='{{.State.Health.Status}}' claw-openclaw 2>/dev/null | grep -q healthy; do sleep 2; i=$$((i+1)); done
	@echo "$(GREEN)✓ OpenClaw 已重启：http://127.0.0.1:10300$(RESET)"

_claw_status:
	@docker ps --filter "name=claw-openclaw" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

_claw_logs:
	@docker logs -f --tail=200 claw-openclaw

# ─── make agent <action> ──────────────────────────────────────────────────────

.PHONY: agent
agent:
	@$(MAKE) --no-print-directory _agent_$(or $(SUBCMD),_help)

.PHONY: _agent_start _agent_stop _agent_restart _agent_status _agent_logs _agent__help
_agent__help:
	@echo "用法：make agent {start|stop|restart|status|logs}"

_agent_start: _check-docker
	@if [ -n "$$(docker ps -q --filter 'name=claw-teamlab')" ]; then \
		echo "$(BOLD)>> Agent 已运行，构建新镜像并重建容器（应用最新改动）...$(RESET)"; \
		$(COMPOSE_FULL) build claw-teamlab; \
		$(COMPOSE_FULL) up -d --force-recreate --no-deps --wait claw-teamlab; \
		echo "$(GREEN)✓ Agent 已更新：http://127.0.0.1:10301$(RESET)"; \
	else \
		echo "$(BOLD)>> 构建 Agent 镜像（claw-teamlab）...$(RESET)"; \
		$(COMPOSE_FULL) build claw-teamlab; \
		echo "$(BOLD)>> 启动 Agent 容器（等待 healthy）...$(RESET)"; \
		$(COMPOSE_FULL) up -d --no-deps --wait claw-teamlab || $(MAKE) --no-print-directory _wait-agent; \
		echo "$(GREEN)✓ Agent 已启动：http://127.0.0.1:10301$(RESET)"; \
	fi

_agent_stop:
	@docker stop claw-teamlab 2>/dev/null && docker rm claw-teamlab 2>/dev/null || true
	@echo "$(GREEN)✓ Agent 容器已停止$(RESET)"

_agent_restart:
	@echo "$(BOLD)>> 构建最新 Agent 镜像（缓存加速）...$(RESET)"
	@$(COMPOSE_FULL) build claw-teamlab
	@echo "$(BOLD)>> 重建容器（应用最新镜像+代码+配置）...$(RESET)"
	@$(COMPOSE_FULL) up -d --force-recreate --no-deps --wait claw-teamlab
	@echo "$(GREEN)✓ Agent 已重启：http://127.0.0.1:10301$(RESET)"

_agent_status:
	@docker ps --filter "name=claw-teamlab" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

_agent_logs:
	@echo "$(BOLD)Agent 日志（$(TEAMLAB_DIR)/data/logs/）：$(RESET)"
	@ls -lht $(TEAMLAB_DIR)/data/logs/teamlab_*.log 2>/dev/null | head -5 || echo "(暂无日志)"
	@echo ""
	@tail -f $(TEAMLAB_DIR)/data/logs/teamlab_web.log 2>/dev/null || echo "(teamlab_web.log 尚未生成，请稍候)"

# ─── make backend <action> ────────────────────────────────────────────────────

.PHONY: backend
backend:
	@$(MAKE) --no-print-directory _backend_$(or $(SUBCMD),_help)

.PHONY: _backend_start _backend_stop _backend_restart _backend_status _backend_logs _backend__help
_backend__help:
	@echo "用法：make backend {start|stop|restart|status|logs}"

_backend_start:
	@if pgrep -f "claw_teamlab.*python main\.py" > /dev/null 2>&1; then \
		PIDS=$$(pgrep -f "claw_teamlab.*python main\.py" | tr '\n' ',' | sed 's/,$//'); \
		echo "$(YELLOW)⚠ 宿主机 Agent 已在运行（pid: $$PIDS），跳过启动$(RESET)"; \
		echo "  如需重启请运行：make backend restart"; \
	else \
		echo "$(BOLD)>> 启动宿主机 Agent（conda: claw_teamlab）...$(RESET)"; \
		cd $(TEAMLAB_DIR) && conda run -n claw_teamlab python main.py all & \
		echo "$(GREEN)✓ 宿主机 Agent 已在后台启动$(RESET)"; \
		echo "  日志：$(TEAMLAB_DIR)/data/logs/"; \
		echo "  状态：make backend status"; \
	fi

_backend_stop:
	@echo "$(BOLD)>> 停止宿主机 Agent...$(RESET)"
	@pkill -TERM -f "claw_teamlab.*python main\.py" 2>/dev/null || true
	@pkill -TERM -f "python main\.py (all|web|workers|scheduler)" 2>/dev/null || true
	@pkill -TERM -f "conda run -n claw_teamlab" 2>/dev/null || true
	@sleep 2
	@pkill -9 -f "claw_teamlab.*python main\.py" 2>/dev/null || true
	@pkill -9 -f "python main\.py (all|web|workers|scheduler)" 2>/dev/null || true
	@pkill -9 -f "conda run -n claw_teamlab" 2>/dev/null || true
	@echo "$(GREEN)✓ 宿主机 Agent 已停止$(RESET)"

_backend_restart:
	@$(MAKE) --no-print-directory _backend_stop
	@sleep 1
	@$(MAKE) --no-print-directory _backend_start

_backend_status:
	@echo "$(BOLD)=== 宿主机 Agent 进程状态 ===$(RESET)"
	@if pgrep -f "claw_teamlab.*python main\.py" > /dev/null 2>&1; then \
		echo "$(GREEN)● 运行中$(RESET)"; \
		ps aux | grep -E "claw_teamlab.*main\.py|python main\.py (all|web|workers|scheduler)" | grep -v grep | \
			awk '{printf "  pid=%-7s 启动时间=%-8s %s %s\n", $$2, $$9, $$11, $$12}'; \
	else \
		echo "$(RED)○ 未运行$(RESET)"; \
	fi

_backend_logs: _agent_logs  # 日志路径相同，复用 _agent_logs

# ─── 综合日志 ─────────────────────────────────────────────────────────────────

.PHONY: logs
logs:
	@if [ "$(firstword $(MAKECMDGOALS))" != "logs" ]; then :; else \
		echo "$(BOLD)=== OpenClaw 容器日志（最近 50 行）===$(RESET)"; \
		docker logs --tail=50 claw-openclaw 2>/dev/null || echo "(容器未运行)"; \
		echo ""; \
		echo "$(BOLD)=== Agent 文件日志（$(TEAMLAB_DIR)/data/logs/）===$(RESET)"; \
		ls -lht $(TEAMLAB_DIR)/data/logs/teamlab_*.log 2>/dev/null | head -5 || echo "(暂无)"; \
	fi

# ─── 状态与健康 ───────────────────────────────────────────────────────────────

.PHONY: health
health:
	@echo "$(BOLD)服务健康检查：$(RESET)"
	@printf "  OpenClaw  :10300  "; \
		curl -sf -o /dev/null --max-time 3 http://localhost:10300/healthz \
		&& echo "$(GREEN)OK$(RESET)" || echo "$(RED)FAIL$(RESET)"
	@printf "  TeamLab   :10301  "; \
		curl -sf -o /dev/null --max-time 3 http://localhost:10301/api/system/status \
		&& echo "$(GREEN)OK$(RESET)" || echo "$(RED)FAIL$(RESET)"
	@printf "  Scheduler :10302  "; \
		curl -sf -o /dev/null --max-time 3 http://localhost:10302/health \
		&& echo "$(GREEN)OK$(RESET)" || echo "$(RED)FAIL$(RESET)"

.PHONY: status
status:
	@if [ "$(firstword $(MAKECMDGOALS))" != "status" ]; then :; else \
		echo "$(BOLD)Docker 容器状态：$(RESET)"; \
		docker ps --filter "name=claw-openclaw" --filter "name=claw-teamlab" \
			--format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || echo "(Docker 未运行)"; \
	fi

# ─── 飞书与配对 ───────────────────────────────────────────────────────────────

.PHONY: approve-device
approve-device:
	@echo "$(BOLD)>> 批准最新待配对设备（Control UI）...$(RESET)"
	@docker exec claw-openclaw openclaw devices approve --latest --token tony_teamlab
	@echo "$(GREEN)✓ 已批准。浏览器打开：http://127.0.0.1:10300/chat?session=main&token=tony_teamlab$(RESET)"

.PHONY: feishu-pair
feishu-pair:
	@if [ -z "$(CODE)" ]; then \
		echo "$(BOLD)飞书配对步骤：$(RESET)"; \
		echo "  1. 在飞书找到机器人，发送任意消息"; \
		echo "  2. 机器人回复配对码（如 123-456）"; \
		echo "  3. 执行：$(GREEN)make feishu-pair CODE=123-456$(RESET)"; \
	else \
		echo "$(BOLD)>> 批准飞书配对码 $(CODE)...$(RESET)"; \
		docker exec claw-openclaw openclaw pairing approve feishu $(CODE) 2>/dev/null \
		|| docker exec claw-openclaw node dist/index.js pairing approve feishu $(CODE); \
		echo "$(GREEN)✓ 配对完成$(RESET)"; \
	fi

.PHONY: feishu-pair-list
feishu-pair-list:
	@docker exec claw-openclaw openclaw pairing list feishu 2>/dev/null \
		|| docker exec claw-openclaw node dist/index.js pairing list feishu

.PHONY: reset-sessions
reset-sessions:
	@echo "$(BOLD)>> 清除 OpenClaw 会话缓存...$(RESET)"
	@rm -f config/agents/main/sessions/*.jsonl \
		config/agents/main/sessions/*.jsonl.lock \
		config/agents/main/sessions/sessions.json 2>/dev/null || true
	@echo "$(GREEN)✓ 已清除（session + lock + sessions.json）$(RESET)"
	@echo "  执行 make claw restart 或 make up-all 后新建会话测试"

# ─── 维护 ─────────────────────────────────────────────────────────────────────

.PHONY: pull
pull:
	@OPENCLAW_IMAGE=$(OPENCLAW_IMAGE) $(COMPOSE_OC) pull claw-openclaw
	@echo "$(GREEN)✓ 镜像已更新，执行 make claw restart 生效$(RESET)"

.PHONY: clean-logs
clean-logs:
	@echo "$(BOLD)>> 清理 30 天前历史日志...$(RESET)"
	@find $(TEAMLAB_DIR)/data/logs/ -name "*.log.*" -mtime +30 -delete 2>/dev/null || true
	@echo "$(GREEN)✓ 清理完成$(RESET)"
	@ls -lht $(TEAMLAB_DIR)/data/logs/ 2>/dev/null | head -8

.PHONY: shell-oc
shell-oc:
	@docker exec -it claw-openclaw /bin/sh

# ─── 内部辅助 ─────────────────────────────────────────────────────────────────

.PHONY: _check-docker
_check-docker:
	@if ! docker info &>/dev/null 2>&1; then \
		echo "$(RED)ERROR: Docker 未就绪$(RESET)"; \
		echo "  请先启动 Docker Desktop，等菜单栏鲸鱼图标稳定后重试"; \
		exit 1; \
	fi

.PHONY: _check-feishu
_check-feishu:
	@if [ ! -f config/openclaw.json ]; then \
		echo "$(YELLOW)WARN: config/openclaw.json 不存在，OpenClaw 将以初始状态启动$(RESET)"; \
		echo "$(YELLOW)      首次部署请复制模板：cp config.example/openclaw.json config/openclaw.json$(RESET)"; \
	else \
		python3 -c "import json,sys; d=json.load(open('config/openclaw.json')); f=d.get('channels',{}).get('feishu',{}); a=f.get('appId') or (f.get('accounts',{}).get('main',{}).get('appId') if isinstance(f.get('accounts'),dict) else None); sys.exit(0 if a else 1)" \
		|| (echo "$(RED)ERROR: config/openclaw.json 存在但未配置 feishu.appId，请编辑 claw/config/openclaw.json$(RESET)" && exit 1); \
	fi

# 仅在 Docker 容器已停止后调用：杀死仍占用端口的宿主机进程
.PHONY: _kill-host-ports
_kill-host-ports:
	@# 第一步：杀宿主机 backend 父进程（python main.py all 等），防止子进程被重新拉起
	@pkill -TERM -f "claw_teamlab.*python main\.py" 2>/dev/null || true; \
	pkill -TERM -f "python main\.py (all|web|workers|scheduler)" 2>/dev/null || true; \
	pkill -TERM -f "conda run -n claw_teamlab" 2>/dev/null || true
	@# 第二步：等待进程退出
	@NEED_WAIT=0; \
	for port in 10300 10301 10302; do \
		pids=$$(lsof -nP -iTCP:$$port -sTCP:LISTEN -t 2>/dev/null | sort -u); \
		if [ -n "$$pids" ]; then NEED_WAIT=1; fi; \
	done; \
	if [ "$$NEED_WAIT" = "1" ]; then sleep 2; fi
	@# 第三步：SIGTERM 仍存活的端口占用者
	@NEED_KILL=0; \
	for port in 10300 10301 10302; do \
		pids=$$(lsof -nP -iTCP:$$port -sTCP:LISTEN -t 2>/dev/null | sort -u); \
		for pid in $$pids; do \
			printf "  端口 %s 仍被占用（pid=%s），SIGTERM...\n" "$$port" "$$pid"; \
			kill -TERM $$pid 2>/dev/null || true; \
			NEED_KILL=1; \
		done; \
	done; \
	if [ "$$NEED_KILL" = "1" ]; then sleep 2; fi
	@# 第四步：SIGKILL 顽固进程
	@for port in 10300 10301 10302; do \
		pids=$$(lsof -nP -iTCP:$$port -sTCP:LISTEN -t 2>/dev/null | sort -u); \
		for pid in $$pids; do \
			printf "  端口 %s 仍被占用（pid=%s），SIGKILL...\n" "$$port" "$$pid"; \
			kill -9 $$pid 2>/dev/null || true; \
		done; \
	done

.PHONY: _wait-oc
_wait-oc:
	@echo "$(BOLD)>> 等待 OpenClaw 就绪（最多 90s）...$(RESET)"
	@for i in $$(seq 1 18); do \
		sleep 5; \
		OC=$$(curl -sf -o /dev/null --max-time 3 http://localhost:10300/healthz && echo ok || echo -); \
		echo "  [$$i/18] OpenClaw: $$OC"; \
		if [ "$$OC" = "ok" ]; then break; fi; \
	done

.PHONY: _wait-agent
_wait-agent:
	@echo "$(BOLD)>> 等待 Agent 就绪（最多 90s）...$(RESET)"
	@for i in $$(seq 1 18); do \
		sleep 5; \
		TL=$$(curl -sf -o /dev/null --max-time 3 http://localhost:10301/api/system/status && echo ok || echo -); \
		echo "  [$$i/18] TeamLab: $$TL"; \
		if [ "$$TL" = "ok" ]; then break; fi; \
	done

.PHONY: _wait-full
_wait-full:
	@echo "$(BOLD)>> 等待全部服务就绪（最多 120s）...$(RESET)"
	@for i in $$(seq 1 24); do \
		sleep 5; \
		OC=$$(curl -sf -o /dev/null --max-time 3 http://localhost:10300/healthz && echo ok || echo -); \
		TL=$$(curl -sf -o /dev/null --max-time 3 http://localhost:10301/api/system/status && echo ok || echo -); \
		echo "  [$$i/24] OpenClaw: $$OC  |  TeamLab: $$TL"; \
		if [ "$$OC" = "ok" ] && [ "$$TL" = "ok" ]; then break; fi; \
	done
