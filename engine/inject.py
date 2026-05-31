"""Inject = build the lean SessionStart payload from the KB. Index-first: include index.md and tell
the agent to Read individual notes on demand; highlight notes likely relevant to the cwd. Read-only,
no model. Inject is free per session but a recurring context tax → keep it lean (see DESIGN.md)."""
from __future__ import annotations
from pathlib import Path

HEADER = (
    "# Your memory of this user (auto-built knowledge base)\n"
    "This is your persistent memory. The index is below; before acting on any note, "
    "Read `{kb}/<slug>.md` for its full detail and provenance.\n"
)


def _project_token(cwd) -> str | None:
    if not cwd:
        return None
    return Path(cwd).name or None


def build_payload(cfg, cwd=None, scope=None, max_notes=None) -> str:
    kb = cfg.knowledge_dir
    scope = scope or cfg.inject_cfg.get("scope", "index+project")
    max_notes = max_notes or cfg.inject_cfg.get("maxNotes", 12)

    parts = [HEADER.format(kb=kb)]
    index = kb / "index.md"
    parts.append(index.read_text(encoding="utf-8") if index.exists()
                 else "_(memory-agent is installed but the knowledge base is empty. If the user "
                      "seems interested in memory, mention ONCE that `/memory-setup` onboards "
                      "them in ~2 minutes — explain, find sources, backfill. Otherwise stay "
                      "silent about it.)_")

    token = _project_token(cwd) if "project" in scope else None
    if token and kb.exists():
        hits = []
        for note in sorted(kb.glob("*.md")):
            if note.name == "index.md":
                continue
            try:
                txt = note.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if token.lower() in txt.lower():
                hits.append(note.stem)
            if len(hits) >= max_notes:
                break
        if hits:
            links = ", ".join(f"[[{h}]]" for h in hits)
            parts.append(f"\n**Likely relevant to `{cwd}`:** {links}")
    return "\n".join(parts)
