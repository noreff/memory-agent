"""Agent-adapter base: a capability descriptor + thin shims. The universal core depends only on
this contract; host-specific behavior (sidechain filtering, native hook output) lives in subclasses.
See adapter.md for the contract."""
from __future__ import annotations
import sys
from pathlib import Path
from typing import Iterator


class AgentAdapter:
    # capability declaration — subclasses override
    cap_capture = "poll"      # "hook" | "watch" | "poll"
    cap_inject = "file"       # "hook" | "file"
    cap_compute = None        # "subagents" | None
    glob = "*.jsonl"          # transcript glob under transcripts_dir

    def __init__(self, name: str, transcripts_dir=None, fmt="auto", inject_file=None, entry=None):
        self.name = name
        self.transcripts_dir = Path(transcripts_dir).expanduser() if transcripts_dir else None
        self.fmt = fmt
        self.inject_file = Path(inject_file).expanduser() if inject_file else None
        self.entry = entry or {}

    # ---- capture (universal: glob the transcripts dir) ----
    def iter_transcripts(self) -> Iterator[Path]:
        if not self.transcripts_dir or not self.transcripts_dir.exists():
            return
        yield from (p for p in sorted(self.transcripts_dir.rglob(self.glob)) if p.is_file())

    def is_capturable(self, path: Path) -> bool:
        """Filter host noise. Default keeps everything; override to skip e.g. subagent transcripts."""
        return True

    def source_id(self, path: Path) -> str:
        return path.stem

    # ---- inject delivery ----
    def deliver_inject(self, payload: str, stdin: dict | None = None) -> None:
        """Universal fallback: write the payload to a file the host loads at startup.
        Native subclasses (e.g. Claude Code) override to emit the host's hook JSON instead."""
        if not self.inject_file:
            sys.stdout.write(payload)
            return
        self.inject_file.parent.mkdir(parents=True, exist_ok=True)
        self.inject_file.write_text(payload, encoding="utf-8")

    @classmethod
    def from_config(cls, entry: dict) -> "AgentAdapter":
        t = entry.get("transcripts", {}) or {}
        a = cls(
            name=entry.get("name", "generic"),
            transcripts_dir=t.get("dir"),
            fmt=t.get("format", "auto"),
            inject_file=(entry.get("inject") or {}).get("file"),
            entry=entry,
        )
        if t.get("glob"):
            a.glob = t["glob"]
        return a

    def __repr__(self):
        return (f"<{self.name} capture={self.cap_capture} inject={self.cap_inject} "
                f"compute={self.cap_compute}>")
