#!/usr/bin/env python3
"""Claude Code SessionStart hook → inject relevant memory as additionalContext (native inject)."""
import json
import sys
from pathlib import Path

_root = Path(__file__).resolve()
while not (_root / "config.json").exists() and _root != _root.parent:
    _root = _root.parent
sys.path.insert(0, str(_root))

from core import config as cfgmod                                  # noqa: E402
from engine.inject import build_payload                            # noqa: E402
from adapters.agent.claude_code.adapter import ClaudeCodeAdapter   # noqa: E402


def main():
    try:
        stdin = json.load(sys.stdin)
    except Exception:
        stdin = {}
    cfg = cfgmod.load()
    payload = build_payload(cfg, cwd=stdin.get("cwd"))
    ClaudeCodeAdapter(name="claude-code").deliver_inject(payload, stdin)


if __name__ == "__main__":
    main()
