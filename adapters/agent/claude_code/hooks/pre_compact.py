#!/usr/bin/env python3
"""Claude Code PreCompact hook → enqueue the session before compaction (safety net). The on-disk
log is not lost to compaction, but capturing here lowers latency. Idempotent with SessionEnd."""
import json
import sys
from pathlib import Path

_root = Path(__file__).resolve()
while not (_root / "config.json").exists() and _root != _root.parent:
    _root = _root.parent
sys.path.insert(0, str(_root))

from core import config as cfgmod                                  # noqa: E402
from engine.capture import enqueue_path                            # noqa: E402
from adapters.agent.claude_code.adapter import ClaudeCodeAdapter   # noqa: E402


def main():
    try:
        stdin = json.load(sys.stdin)
    except Exception:
        stdin = {}
    tp = stdin.get("transcript_path")
    if not tp:
        return
    cfg = cfgmod.load()
    enqueue_path(ClaudeCodeAdapter(name="claude-code"), cfg, tp, via="hook")


if __name__ == "__main__":
    main()
