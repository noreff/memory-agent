#!/bin/sh
# memory-agent background refresh: capture + growth-gated local extraction, OUTSIDE any chat
# session — fully silent (logs to state/logs/). The strong-model merge is NOT run here (it rides
# an open Claude Code session via /memory-refresh); atoms simply accumulate until one drains them.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/state/logs"
mkdir -p "$LOG_DIR"
{
  echo "── $(date '+%Y-%m-%d %H:%M:%S') refresh cycle"
  /usr/bin/python3 "$ROOT/mem.py" capture
  # Background extraction is LOCAL-ONLY by policy: a daemon must never shell out to the Claude
  # CLI headlessly. No local server -> capture only; atoms extract on the next in-session
  # /memory-refresh (which may use any backend, because the user initiated it).
  if curl -s -m 2 "http://localhost:1234/v1/models" > /dev/null 2>&1; then
    /usr/bin/python3 "$ROOT/mem.py" refresh --backend local --min-growth 75000
  else
    echo "local model server unreachable — capture only this cycle"
  fi
  /usr/bin/python3 "$ROOT/mem.py" merge --stage check
} >> "$LOG_DIR/refresh.log" 2>&1
# keep the log bounded (~last 2000 lines)
tail -n 2000 "$LOG_DIR/refresh.log" > "$LOG_DIR/refresh.log.tmp" && mv "$LOG_DIR/refresh.log.tmp" "$LOG_DIR/refresh.log"
