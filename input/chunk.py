"""Mechanical input-handler: turn a raw transcript into clean, chunked text for a completion backend
(the fallback when no tool-using ingest agent is available). Self-discovers a couple of common shapes:
Claude Code / OpenCode `.jsonl` (one JSON record per line) and plain text/markdown.

Drops noise (sidechain/subagent turns, tool-call payloads, system reminders) but keeps a few lines of
each tool RESULT — that is where durable facts (paths, ports, installed services) live.

Echo suppression: injected memory (our own SessionStart payload, MEMORY.md system-reminders, slash-
command wrappers) is stripped BEFORE extraction, so the extractor never re-mines the memory system's
own output back into atoms (the memory → session → memory feedback loop)."""
from __future__ import annotations
import json
import re
from pathlib import Path

_SYS_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>\s*", re.DOTALL)
_CMD_WRAPPER_RE = re.compile(
    r"<(command-name|command-message|command-args|local-command-stdout|local-command-caveat)>"
    r".*?</\1>\s*", re.DOTALL)
_MEM_HEADER = "# Your memory of this user (auto-built knowledge base)"


def _strip_injected(text: str) -> str:
    from core.config import SENTINEL
    if SENTINEL in text:  # the system's own internal model call — never re-memorize it
        return ""
    text = _SYS_REMINDER_RE.sub("", text)
    text = _CMD_WRAPPER_RE.sub("", text)
    if _MEM_HEADER in text:  # inject payload outside a system-reminder wrapper (belt & suspenders)
        text = text.split(_MEM_HEADER)[0]
    return text


def _content_text(content, tool_cap: int) -> str:
    if isinstance(content, str):
        return _strip_injected(content)
    if not isinstance(content, list):
        return ""
    parts = []
    for b in content:
        if not isinstance(b, dict):
            parts.append(str(b))
            continue
        bt = b.get("type")
        if bt == "text":
            parts.append(_strip_injected(b.get("text", "")))
        elif bt == "tool_result":
            c = b.get("content")
            if isinstance(c, list):
                txt = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
            else:
                txt = c if isinstance(c, str) else ""
            capped = "\n".join(txt.splitlines()[:tool_cap]).strip()
            if capped:
                parts.append(f"[tool result] {capped}")
        # tool_use payloads are dropped
    return "\n".join(p for p in parts if p)


def distill_jsonl(path, tool_cap: int = 6, offset: int = 0):
    """Return (clean_text, date, end_offset) from a .jsonl transcript; skips sidechain/subagent
    turns. `offset` (bytes) supports tail-only re-processing of append-only logs: pass the offset
    a previous run returned and only the new records are read."""
    with Path(path).open("rb") as f:
        f.seek(offset)
        data = f.read()
    end_offset = offset + len(data)
    lines, date = [], None
    for raw in data.decode("utf-8", errors="ignore").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            o = json.loads(raw)
        except Exception:
            continue
        if o.get("isSidechain"):
            continue
        ts = o.get("timestamp")
        if ts and not date:
            date = str(ts)[:10]
        if o.get("type") not in ("user", "assistant"):
            continue
        msg = o.get("message") or {}
        text = _content_text(msg.get("content"), tool_cap)
        if text.strip():
            lines.append(f"{msg.get('role', o['type']).upper()}: {text.strip()}")
    return "\n\n".join(lines), (date or "unknown"), end_offset


def distill(path, fmt="auto", offset: int = 0):
    """Dispatch on format → (text, date, end_offset). 'auto' sniffs by extension. Only line-based
    append-only formats honor `offset`; other files are always read whole."""
    p = Path(path)
    if fmt in ("claude-code-jsonl", "opencode", "jsonl") or p.suffix == ".jsonl":
        return distill_jsonl(p, offset=offset)
    text = p.read_text(encoding="utf-8", errors="ignore")
    return text, "unknown", len(text.encode("utf-8"))


def chunk_text(text: str, words: int = 2800):
    """Split on line boundaries, preserving the USER:/ASSISTANT: structure distill built —
    flattening newlines measurably degrades extraction."""
    if len(text.split()) <= words:
        return [text]
    chunks, cur, n = [], [], 0
    for ln in text.splitlines():
        w = len(ln.split())
        if n + w > words and cur:
            chunks.append("\n".join(cur))
            cur, n = [], 0
        cur.append(ln)
        n += w
    if cur:
        chunks.append("\n".join(cur))
    return chunks or [text]
