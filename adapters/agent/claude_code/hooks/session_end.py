#!/usr/bin/env python3
"""Claude Code SessionEnd hook → enqueue the finished session (compute-free, fire-and-forget)."""
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
    # Event-driven freshness: nudge the daemon NOW instead of waiting for the hourly tick, so the
    # just-finished session is extracted+placed within minutes. Detached and best-effort: launchd
    # no-ops if the job is already running, flock serializes the pipeline, and a missing service
    # (daemon not installed) must never break the hook.
    try:
        import os
        import subprocess
        subprocess.Popen(
            ["launchctl", "kickstart", f"gui/{os.getuid()}/com.memory-agent.refresh"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


if __name__ == "__main__":
    main()
