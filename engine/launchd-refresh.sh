#!/bin/sh
# memory-agent autonomous cycle (launchd): capture -> local extract -> ingest-drain, $0, offline.
# Resumable + crash-safe: durable atoms, flock auto-released on death, skip-on-failure in ingest.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/state/logs"
mkdir -p "$LOG_DIR"
LMS="$HOME/.lmstudio/bin/lms"
{
  echo "── $(date '+%Y-%m-%d %H:%M:%S') cycle"
  # ensure local models loaded with adequate context (idempotent: only loads if missing)
  if [ -x "$LMS" ]; then
    "$LMS" ps 2>/dev/null | grep -q "qwen3.6-35b-a3b" || "$LMS" load qwen/qwen3.6-35b-a3b --context-length 32768 -y 2>/dev/null || true
    "$LMS" ps 2>/dev/null | grep -q "qwen3.6-27b"     || "$LMS" load qwen/qwen3.6-27b --context-length 65536 -y 2>/dev/null || true
  fi
  /usr/bin/python3 "$ROOT/mem.py" cycle
} >> "$LOG_DIR/refresh.log" 2>&1
tail -n 2000 "$LOG_DIR/refresh.log" > "$LOG_DIR/refresh.log.tmp" && mv "$LOG_DIR/refresh.log.tmp" "$LOG_DIR/refresh.log"
