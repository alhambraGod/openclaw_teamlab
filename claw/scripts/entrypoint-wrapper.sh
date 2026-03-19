#!/bin/sh
# Wrapper entrypoint for claw-openclaw container.
# 1. Starts a TCP proxy (127.0.0.1:10301 -> claw-teamlab:10301) in background
# 2. Exec's the original docker-entrypoint.sh with all arguments

PROXY_SCRIPT="/opt/teamlab-proxy.mjs"

if [ -f "$PROXY_SCRIPT" ]; then
  node "$PROXY_SCRIPT" &
fi

exec docker-entrypoint.sh "$@"
