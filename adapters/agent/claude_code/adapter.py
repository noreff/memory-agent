"""Claude Code adapter: all three capabilities native (lifecycle hooks, SessionStart inject,
subscription subagents). Subscription compute is in-session only — see DESIGN.md 'прикол'."""
from __future__ import annotations
import json
import sys
from pathlib import Path

from ..base import AgentAdapter

DEFAULT_DIR = "~/.claude/projects"


class ClaudeCodeAdapter(AgentAdapter):
    cap_capture = "hook"      # native; poll is the backstop
    cap_inject = "hook"
    cap_compute = "subagents"
    glob = "*.jsonl"

    @classmethod
    def from_config(cls, entry: dict) -> "ClaudeCodeAdapter":
        t = entry.get("transcripts", {}) or {}
        return cls(
            name=entry.get("name", "claude-code"),
            transcripts_dir=t.get("dir", DEFAULT_DIR),
            fmt=t.get("format", "claude-code-jsonl"),
            entry=entry,
        )

    def is_capturable(self, path: Path) -> bool:
        """Keep only top-level human sessions. A main session's records carry isSidechain:false;
        subagent transcripts carry isSidechain:true (≈75-85% of files). Cheap: read first 8KB only."""
        try:
            head = path.open("rb").read(8192).decode("utf-8", "ignore")
        except OSError:
            return False
        if '"isSidechain":false' in head:
            return True
        if '"isSidechain":true' in head:
            return False
        return True  # no marker yet (ambiguous) → keep; the ingest step filters sidechain content

    def deliver_inject(self, payload: str, stdin: dict | None = None) -> None:
        """Native SessionStart contract: emit additionalContext as JSON, suppressed from user view."""
        out = {
            "continue": True,
            "suppressOutput": True,
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": payload,
            },
        }
        sys.stdout.write(json.dumps(out))
