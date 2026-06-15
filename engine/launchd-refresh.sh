#!/bin/sh
# memory-agent autonomous cycle (launchd on macOS / cron on Linux): ONE tick of
# capture -> local extract -> local merge+promote, fully offline, $0, OUTSIDE any chat session.
# All the logic lives in `mem.py cycle` (LOCAL-ONLY by policy — it never shells out to the Claude
# CLI). The strong-model (subscription) merge is now just an OPTIONAL quality pass via the
# in-session /memory-refresh command; freshness no longer depends on a session being open.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/state/logs"
mkdir -p "$LOG_DIR"
{
  echo "── $(date '+%Y-%m-%d %H:%M:%S') cycle"
  /usr/bin/python3 "$ROOT/mem.py" cycle
} >> "$LOG_DIR/refresh.log" 2>&1
# keep the log bounded (~last 2000 lines)
tail -n 2000 "$LOG_DIR/refresh.log" > "$LOG_DIR/refresh.log.tmp" && mv "$LOG_DIR/refresh.log.tmp" "$LOG_DIR/refresh.log"
