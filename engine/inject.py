"""Inject = build the SessionStart payload from the KB. Read-only, no model.

LEAN by default (inject.mode): the recurring per-session context tax used to be the FULL index
(30KB+ into every session of every agent) — now it's a ~4KB core: who-the-user-is notes, cwd-
relevant notes, and the most recently updated notes, plus a pointer to the full index for agentic
search. The index is the interface, not the payload.

The whole payload is wrapped in INJECT_BEGIN/INJECT_END markers. input/chunk.py strips anything
between them when distilling transcripts, so the memory system can never re-mine its own injected
output back into atoms (the memory -> session -> memory echo loop), no matter how the payload's
inner format evolves. Content fingerprints in chunk.py remain as belt-and-suspenders for KB files
read back as tool results."""
from __future__ import annotations
import re
from pathlib import Path

INJECT_BEGIN = "<memory-agent-inject>"
INJECT_END = "</memory-agent-inject>"

HEADER = (
    "# Your memory of this user (auto-built knowledge base)\n"
    "This is your persistent memory: the core below, plus the full index at `{kb}/index.md` — "
    "Read that and grep `{kb}/*.md` for anything beyond the core. Before acting on any note, "
    "Read `{kb}/<slug>.md` for full detail and provenance.\n"
)


def _project_token(cwd) -> str | None:
    if not cwd:
        return None
    return Path(cwd).name or None


def _index_rows(kb):
    """Parse index.md table rows -> [(slug, type, summary_line)]."""
    index = kb / "index.md"
    if not index.exists():
        return []
    rows = []
    for ln in index.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"^\|\s*\[\[([^\]]+)\]\]\s*\|\s*(\S+)\s*\|\s*(.*?)\s*\|$", ln)
        if m:
            rows.append((m.group(1), m.group(2), m.group(3)))
    return rows


def _cwd_hits(kb, token, max_notes):
    hits = []
    for note in sorted(kb.glob("*.md")):
        if note.name in ("index.md", "README.md"):
            continue
        try:
            txt = note.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if token.lower() in txt.lower():
            hits.append(note.stem)
        if len(hits) >= max_notes:
            break
    return hits


def build_payload(cfg, cwd=None, scope=None, max_notes=None) -> str:
    kb = cfg.knowledge_dir
    scope = scope or cfg.inject_cfg.get("scope", "index+project")
    max_notes = max_notes or cfg.inject_cfg.get("maxNotes", 12)
    mode = cfg.inject_cfg.get("mode", "lean")
    budget = int(cfg.inject_cfg.get("maxBytes", 4096))

    parts = [HEADER.format(kb=kb)]
    rows = _index_rows(kb)
    if not rows:
        parts.append("_(memory-agent is installed but the knowledge base is empty. If the user "
                     "seems interested in memory, mention ONCE that `/memory-setup` onboards "
                     "them in ~2 minutes — explain, find sources, backfill. Otherwise stay "
                     "silent about it.)_")
    elif mode == "full":
        parts.append((kb / "index.md").read_text(encoding="utf-8"))
    else:
        # lean: identity core first, then most-recently-updated notes until the byte budget.
        core = [r for r in rows if r[1] in ("user", "feedback")]
        rest = [r for r in rows if r[1] not in ("user", "feedback")]

        def mtime(slug):
            try:
                return (kb / f"{slug}.md").stat().st_mtime
            except OSError:
                return 0
        rest.sort(key=lambda r: mtime(r[0]), reverse=True)
        lines = ["", "| Note | Type | Summary |", "|---|---|---|"]
        used = sum(len(p) for p in parts)
        for slug, ty, summ in core + rest:
            ln = f"| [[{slug}]] | {ty} | {summ} |"
            if used + len(ln) > budget and ty not in ("user", "feedback"):
                break  # identity core always fits; the rest respects the budget
            lines.append(ln)
            used += len(ln) + 1
        shown = len(lines) - 3
        lines.append(f"\n_({shown} of {len(rows)} notes shown — the full index lives at "
                     f"`{kb}/index.md`.)_")
        parts.append("\n".join(lines))

    token = _project_token(cwd) if "project" in scope else None
    if token and kb.exists():
        hits = _cwd_hits(kb, token, max_notes)
        if hits:
            links = ", ".join(f"[[{h}]]" for h in hits)
            parts.append(f"\n**Likely relevant to `{cwd}`:** {links}")
    return INJECT_BEGIN + "\n" + "\n".join(parts) + "\n" + INJECT_END


def write_inject_files(cfg) -> int:
    """Render the payload to every configured inject file (e.g. pi's global AGENTS.md) so promotes
    and compactions keep external agents current. Fully guarded — must never break a caller."""
    n = 0
    try:
        payload = build_payload(cfg)
        for f in (cfg.inject_cfg.get("files") or []):
            p = Path(f).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(p)
            n += 1
    except Exception:
        pass
    return n
