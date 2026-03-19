#!/usr/bin/env bash
# launch.sh — C-Si TeamLab 专属 OpenClaw 启动入口
#
# 这是 Makefile 的兼容包装，推荐直接使用 make 命令：
#   make up       启动
#   make down     停止
#   make status   状态
#   make logs     日志
#   make help     查看所有命令
#
# 直接运行本脚本等同于 make up

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 如果传入了参数，转发给 make
if [ $# -gt 0 ]; then
    exec make "$@"
fi

exec make up
