#!/bin/bash
# Legacy compatibility wrapper — delegates to ./teamlab
# Usage: start.sh {all|start|stop|status|...}
exec "$(dirname "$0")/teamlab" "${@:-start}"

