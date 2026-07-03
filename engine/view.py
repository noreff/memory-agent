"""engine/view — read-only ANSI viewer over the knowledge base. The KB is plain markdown you can
cat and grep; this module is the guided tour: a dashboard, a note renderer, full-text search, and
the two things no other memory system can show — receipts (`why`: every claim with its verbatim
evidence quote and source session) and the supersede log (`conflicts`: what memory used to believe).

Everything here is read-only and stdlib-only. Color is truecolor ANSI, disabled automatically when
stdout is not a tty or NO_COLOR is set (and by --plain), so output stays pipe- and grep-friendly.
"""
from __future__ import annotations
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from engine.merge import fget, parse_note

# ── palette ──────────────────────────────────────────────────────────────────
PALETTE = {
    "fg": "c0caf5", "dim": "565f89", "blue": "7aa2f7", "cyan": "7dcfff",
    "magenta": "bb9af7", "yellow": "e0af68", "orange": "ff9e64", "green": "9ece6a",
    "teal": "73daca", "red": "f7768e", "gray": "9aa5ce",
}
TYPE_COLORS = {
    "project": "blue", "reference": "cyan", "user": "magenta", "decision": "yellow",
    "feedback": "orange", "concept": "green", "entity": "teal", "claim": "gray",
}
SPARK = "▁▂▃▄▅▆▇█"

_color = True


def init(plain=False):
    global _color
    _color = (not plain) and sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def c(text, fg=None, bold=False, dim=False, italic=False):
    if not _color:
        return str(text)
    codes = []
    if bold:
        codes.append("1")
    if dim:
        codes.append("2")
    if italic:
        codes.append("3")
    if fg:
        h = PALETTE.get(fg, fg)
        codes.append(f"38;2;{int(h[0:2], 16)};{int(h[2:4], 16)};{int(h[4:6], 16)}")
    return f"\033[{';'.join(codes)}m{text}\033[0m" if codes else str(text)


_ANSI = re.compile(r"\033\[[0-9;]*m")


def vlen(s):
    return len(_ANSI.sub("", s))


def pad(s, width):
    return s + " " * max(0, width - vlen(s))


def term_width(cap=100):
    cols = shutil.get_terminal_size((cap, 24)).columns
    return min(cols, cap) if cols >= 40 else cap  # degenerate ptys report 0/tiny widths


def wrap_ansi(text, width, indent=""):
    """Greedy word-wrap that measures visible length (ANSI-aware). Continuation lines get
    `indent`. Terminals carry open attributes across newlines, so spans that wrap stay styled."""
    lead = text[:len(text) - len(text.lstrip(" "))]  # preserve the caller's first-line indent
    lines, cur = [], lead
    for w in text.lstrip(" ").split(" "):
        if vlen(cur.strip()) and vlen(cur) + 1 + vlen(w) > width:
            lines.append(cur)
            cur = indent + w
        else:
            cur = cur + w if cur.endswith(" ") or not cur else cur + " " + w
    if cur.strip():
        lines.append(cur)
    return lines or [""]


def badge(note_type):
    return c(f"▪ {note_type or '?'}", TYPE_COLORS.get(note_type, "gray"))


def spark(counts):
    top = max(counts) if counts and max(counts) else 1
    return "".join(SPARK[min(7, int(v / top * 7 + 0.5))] if v else SPARK[0] for v in counts)


# ── data loading ─────────────────────────────────────────────────────────────
def _val(fields, key, default=None):
    v = fget(fields, key)
    return v[1] if v else default


def load_notes(cfg):
    notes = []
    for f in sorted(cfg.knowledge_dir.glob("*.md")):
        if f.name in ("index.md", "README.md", "MEMORY.md"):
            continue
        try:
            fields, body = parse_note(f.read_text())
        except Exception:
            continue
        m = re.search(r"^# (.+)$", body, re.MULTILINE)
        notes.append({
            "slug": f.stem, "path": f,
            "type": _val(fields, "type", "?"),
            "sources": _val(fields, "sources", []) or [],
            "confidence": float(_val(fields, "confidence", 0) or 0),
            "updated": _val(fields, "updated", ""),
            "conflicts": _val(fields, "conflicts", "") or "",
            "links": _val(fields, "links", []) or [],
            "title": m.group(1).strip() if m else f.stem,
            "body": body,
        })
    return notes


def _ledger_dirs(cfg):
    base = cfg.state_dir / "derived"
    return sorted(base.glob("ledgers*")) if base.exists() else []


def load_ledgers(cfg):
    """slug -> [atom, ...] from every space's ledger dir."""
    out = {}
    for d in _ledger_dirs(cfg):
        for f in d.glob("*.atoms.jsonl"):
            slug = f.name[:-len(".atoms.jsonl")]
            atoms = []
            for line in f.read_text().splitlines():
                try:
                    atoms.append(json.loads(line))
                except Exception:
                    pass
            out.setdefault(slug, []).extend(atoms)
    return out


def load_sessions(cfg):
    """source id -> inbox record (has abs transcript path, adapter)."""
    out = {}
    for name in ("done.jsonl", "pending.jsonl"):
        f = cfg.state_dir / "inbox" / name
        if not f.exists():
            continue
        for line in f.read_text().splitlines():
            try:
                rec = json.loads(line)
                out[rec.get("source", "")] = rec
            except Exception:
                pass
    return out


def session_label(source, sessions):
    """Short id plus a human hint of where the session lived."""
    sid = (source or "")[:8]
    rec = sessions.get(source)
    if not rec:
        return sid
    proj = Path(rec.get("abs", "")).parent.name
    if proj.startswith("-"):  # claude-code project dirs encode the cwd path with dashes
        proj = proj.replace("-", "/")
        home = str(Path.home())
        proj = "~" + proj[len(home):] if proj.startswith(home) else proj
    return f"{sid} · {proj}" if proj else sid


def pending_pipeline(cfg):
    """(sessions queued, unrouted atoms) — cheap counts for the dashboard."""
    queued = 0
    f = cfg.state_dir / "inbox" / "pending.jsonl"
    if f.exists():
        queued = sum(1 for ln in f.read_text().splitlines() if ln.strip())
    unrouted = 0
    adir = cfg.state_dir / "derived" / "atoms"
    if adir.exists():
        for af in adir.glob("*.json"):
            try:
                unrouted += sum(1 for a in json.loads(af.read_text()) if "routed" not in a)
            except Exception:
                pass
    return queued, unrouted


def resolve(notes, query):
    """Fuzzy slug resolution: exact -> prefix -> substring -> word match. Returns (note, candidates)."""
    q = query.lower().strip()
    by_slug = {n["slug"]: n for n in notes}
    if q in by_slug:
        return by_slug[q], []
    tiers = [
        [n for n in notes if n["slug"].startswith(q)],
        [n for n in notes if q in n["slug"]],
        [n for n in notes if all(w in n["slug"] or w in n["title"].lower() for w in q.split())],
    ]
    for tier in tiers:
        if len(tier) == 1:
            return tier[0], []
        if len(tier) > 1:
            return None, tier
    return None, []


def _resolve_or_die(notes, query):
    note, candidates = resolve(notes, query)
    if note:
        return note
    if candidates:
        print(c(f"'{query}' matches {len(candidates)} notes:", "yellow"))
        for n in candidates[:10]:
            print(f"  {badge(n['type'])} {c(n['slug'], 'fg', bold=True)}")
        sys.exit(1)
    print(c(f"no note matches '{query}'", "red"))
    sys.exit(1)


# ── markdown rendering ───────────────────────────────────────────────────────
def style_inline(line):
    line = re.sub(r"\*\*(.+?)\*\*", lambda m: c(m.group(1), "fg", bold=True), line)
    line = re.sub(r"`([^`]+)`", lambda m: c(m.group(1), "cyan"), line)
    line = re.sub(r"\[\[([^\]]+)\]\]", lambda m: c("[[" + m.group(1) + "]]", "magenta"), line)
    line = re.sub(r"(https?://\S+)", lambda m: c(m.group(1), "blue", italic=True), line)
    return line


def render_body(body, width):
    out, in_code = [], False
    for raw in body.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            in_code = not in_code
            out.append(c("  " + line, "dim"))
        elif in_code:
            out.append(c("  " + line, "cyan", dim=True))
        elif line.startswith("## Recent (unconsolidated)"):
            out.append("")
            out.append(c("● recent", "yellow", bold=True) + c(" — placed, awaiting consolidation", "yellow", dim=True))
        elif line.startswith("# "):
            out.append(c(line[2:], "blue", bold=True))
        elif line.startswith("## "):
            out.append("")
            out.append(c(line[3:], "fg", bold=True))
        elif line.startswith("### "):
            out.append("")
            out.append(c(line[4:], "gray", bold=True))
        elif re.match(r"^\*\*[^*]+:?\*\*$", line):  # bold pseudo-headers common in synth'd notes
            out.append("")
            out.append(c(line.strip("*").rstrip(":"), "blue", bold=True))
        elif line.startswith("|"):
            out.append("  " + c(line, "dim") if set(line) <= set("|-: ") else "  " + style_inline(line))
        elif re.match(r"^\s*[-*] ", line):
            ind = len(line) - len(line.lstrip())
            text = style_inline(line.lstrip()[2:])
            bullet = " " * ind + "  " + c("•", TYPE_COLORS.get("concept", "green")) + " "
            out.extend(wrap_ansi(bullet + text, width, " " * (ind + 4)))
        elif line.startswith(">"):
            out.append("  " + c("│ " + line.lstrip("> "), "dim", italic=True))
        else:
            out.extend(wrap_ansi("  " + style_inline(line), width, "  ") if line else [""])
    # collapse runs of blank lines
    res = []
    for ln in out:
        if ln == "" and res and res[-1] == "":
            continue
        res.append(ln)
    return res


def parse_conflicts(block):
    """Split a conflicts frontmatter block into (date, text) entries."""
    entries = []
    for chunk in re.split(r"\n\s*\n", block.strip()):
        chunk = " ".join(chunk.split())
        if not chunk:
            continue
        m = re.match(r"^\[(\d{4}-\d{2}-\d{2})\]\s*(.*)$", chunk)
        entries.append((m.group(1), m.group(2)) if m else ("", chunk))
    return entries


def _dots(conf):
    n = max(0, min(5, round(conf * 5)))
    return c("●" * n, "green") + c("○" * (5 - n), "dim")


def _ago(ts):
    days = max(0, (datetime.now() - datetime.fromtimestamp(ts)).days)
    if days == 0:
        h = int((datetime.now() - datetime.fromtimestamp(ts)).total_seconds() // 3600)
        return f"{h}h ago" if h else "just now"
    return f"{days}d ago"


# ── commands ─────────────────────────────────────────────────────────────────
def dashboard(cfg):
    W = term_width()
    notes, ledgers = load_notes(cfg), load_ledgers(cfg)
    if not notes:
        print(c("knowledge base is empty — run a backfill or `mem.py cycle` first", "yellow"))
        return
    atoms_placed = sum(len(v) for v in ledgers.values())
    n_conf = sum(1 for n in notes if n["conflicts"])

    head = c("◆ memory", "blue", bold=True) + c("  ·  ", "dim") + \
        c(f"{len(notes)} notes", "fg", bold=True) + c("  ·  ", "dim") + \
        (c(f"{atoms_placed:,} atoms on ledgers", "fg") + c("  ·  ", "dim") if atoms_placed else "") + \
        c(str(cfg.knowledge_dir).replace(str(Path.home()), "~"), "dim")
    print()
    print(head)
    print()

    # type distribution
    counts = {}
    for n in notes:
        counts[n["type"]] = counts.get(n["type"], 0) + 1
    top = max(counts.values())
    barw = min(34, W - 26)
    for ty, cnt in sorted(counts.items(), key=lambda kv: -kv[1]):
        bar = "█" * max(1, int(cnt / top * barw))
        print(f"  {pad(c(ty, TYPE_COLORS.get(ty, 'gray')), 12)} "
              f"{c(bar, TYPE_COLORS.get(ty, 'gray'))} {c(cnt, 'dim')}")
    print()

    # coverage: atom dates say what span of your history the memory has actually read
    dates = sorted(a.get("date", "") for v in ledgers.values() for a in v if a.get("date"))
    if dates:
        months, order = {}, []
        for d in dates:
            mo = d[:7]
            if mo not in months:
                order.append(mo)
            months[mo] = months.get(mo, 0) + 1
        order = order[-12:]
        vals = [months[m] for m in order]
        print(f"  {c('coverage', 'fg', bold=True)}  {c(spark(vals), 'teal')}  "
              + c(f"{order[0]} → {order[-1]} · peak {max(vals)} atoms/mo", "dim"))
        print()

    # freshest notes / heaviest evidence
    fresh = sorted(notes, key=lambda n: n["updated"], reverse=True)[:5]
    heavy = sorted(notes, key=lambda n: len(n["sources"]), reverse=True)[:5]
    colw = (W - 6) // 2
    print(f"  {pad(c('recently updated', 'fg', bold=True), colw)}  {c('most evidence', 'fg', bold=True)}")
    for f, h in zip(fresh, heavy):
        left = pad(f"  {c(f['slug'][:colw - 12], 'fg')} {c(f['updated'][5:], 'dim')}", colw)
        right = f"  {c(h['slug'][:colw - 12], 'fg')} {c(str(len(h['sources'])) + ' src', 'dim')}"
        print(left + right)
    print()

    queued, unrouted = pending_pipeline(cfg)
    parts = [c("pipeline", "fg", bold=True) + " "]
    parts.append(c(f" {queued} sessions queued", "yellow" if queued else "dim"))
    parts.append(c(" → ", "dim") + c(f"{unrouted} atoms awaiting merge", "yellow" if unrouted else "dim"))
    log = cfg.state_dir / "logs" / "refresh.log"
    if log.exists():
        parts.append(c(" · last refresh ", "dim") + c(_ago(log.stat().st_mtime), "dim"))
    print("  " + "".join(parts))
    if n_conf:
        print("  " + c("conflicts", "fg", bold=True) + "  "
              + c(f"{n_conf} notes carry a supersede log — nothing is silently overwritten", "dim"))
    print()
    print("  " + c("mem view <note> · mem find <query> · mem why <note> · mem conflicts", "dim"))
    print()


def show(cfg, query):
    W = term_width()
    notes = load_notes(cfg)
    note = _resolve_or_die(notes, query)
    ledger = load_ledgers(cfg).get(note["slug"], [])
    inner = W - 4

    top = f"╭─ {note['slug']} " + "─" * max(1, inner - len(note["slug"]) - 3) + "╮"
    meta = (f" {badge(note['type'])}   confidence {_dots(note['confidence'])}   "
            + c(f"updated {note['updated']}", "dim"))
    stats = " " + c(f"{len(note['sources'])} sources", "fg") \
        + (c("  ·  ", "dim") + c(f"{len(ledger)} atoms on ledger", "fg") if ledger else "") \
        + (c("  ·  ", "dim") + c(f"{len(parse_conflicts(note['conflicts']))} conflicts", "yellow")
           if note["conflicts"] else "")
    print()
    print(c(top, "dim"))
    for line in (meta, stats):
        print(c("│", "dim") + pad(line, inner) + c("│", "dim"))
    print(c("╰" + "─" * inner + "╯", "dim"))
    print()

    body = re.sub(r"^# .+\n", "", note["body"].strip() + "\n", count=1)  # header card owns the title
    for line in render_body(body.strip(), W - 2):
        print(line)
    print()

    entries = parse_conflicts(note["conflicts"])
    if entries:
        print(c("⚡ conflict log", "yellow", bold=True)
              + c(" — what this note used to say", "yellow", dim=True))
        for date, text in entries[:3]:
            head = c(date or "earlier", "yellow") + "  "
            print()
            for ln in wrap_ansi("  " + head + c(text, "gray"), W - 2, "  " + " " * (len(date or "earlier") + 2)):
                print(ln)
        if len(entries) > 3:
            print()
            print("  " + c(f"… {len(entries) - 3} earlier entries", "dim"))
        print()
    if note["links"]:
        print(c("linked:", "dim"), " ".join(c(f"[[{l}]]", "magenta") for l in note["links"]))
        print()


def list_notes(cfg, type_filter=None, sort="updated", limit=30):
    notes = load_notes(cfg)
    ledgers = load_ledgers(cfg)
    if type_filter:
        notes = [n for n in notes if n["type"] == type_filter]
    key = {"updated": lambda n: n["updated"],
           "sources": lambda n: len(n["sources"]),
           "conflicts": lambda n: len(parse_conflicts(n["conflicts"])),
           "atoms": lambda n: len(ledgers.get(n["slug"], []))}[sort]
    notes.sort(key=key, reverse=True)
    shown = notes[:limit] if limit else notes
    print()
    print(c(f"◆ {len(notes)} notes", "blue", bold=True)
          + (c(f" · type={type_filter}", "dim") if type_filter else "")
          + c(f" · sorted by {sort}", "dim"))
    print()
    for n in shown:
        marks = c(" ⚡", "yellow") if n["conflicts"] else ""
        atoms = len(ledgers.get(n["slug"], []))
        srcs = str(len(n["sources"])) + " src"
        print(f"  {pad(badge(n['type']), 12)} {pad(c(n['slug'], 'fg'), 44)} "
              f"{c(n['updated'], 'dim')}  {c(srcs, 'dim')}"
              + (c(f" {atoms} atoms", "dim") if atoms else "") + marks)
    if len(notes) > len(shown):
        print("  " + c(f"… {len(notes) - len(shown)} more (--limit 0 for all)", "dim"))
    print()


def find(cfg, terms):
    """Terms are AND-ed per note (all must appear somewhere in slug+body); a line matches if it
    contains any term. An exact-phrase hit outranks scattered terms."""
    W = term_width()
    q = " ".join(terms).strip()
    words = [re.compile(re.escape(w), re.IGNORECASE) for w in q.split()]
    rx = re.compile("|".join(re.escape(w) for w in q.split()), re.IGNORECASE)
    phrase = re.compile(re.escape(q), re.IGNORECASE)
    notes, hits = load_notes(cfg), []
    for n in notes:
        hay = n["slug"] + "\n" + n["body"]
        if not all(w.search(hay) for w in words):
            continue
        matches = [ln for ln in n["body"].splitlines() if rx.search(ln)]
        score = len(matches) + (20 if phrase.search(hay) else 0) \
            + (5 if rx.search(n["slug"]) or rx.search(n["title"]) else 0)
        matches.sort(key=lambda ln: 0 if phrase.search(ln) else 1)
        hits.append((score, n, matches))
    hits.sort(key=lambda h: -h[0])
    print()
    print(c("◆ find", "blue", bold=True) + f" {c(repr(q), 'fg')} "
          + c(f"— {len(hits)} notes match", "dim"))
    print()
    hl = lambda m: c(m.group(0), "yellow", bold=True)
    for score, n, matches in hits[:8]:
        print(f"  {pad(badge(n['type']), 12)} {c(n['slug'], 'fg', bold=True)} "
              + c(f"({len(matches)} lines)", "dim"))
        for ln in matches[:2]:
            ln = " ".join(ln.split())
            if len(ln) > W - 8:
                pos = max(0, (rx.search(ln).start() if rx.search(ln) else 0) - 20)
                ln = ("…" if pos else "") + ln[pos:pos + W - 10] + "…"
            print("      " + rx.sub(hl, c(ln, "gray")))
    if len(hits) > 8:
        print("  " + c(f"… {len(hits) - 8} more notes", "dim"))
    print()


def why(cfg, query, limit=10):
    W = term_width()
    notes = load_notes(cfg)
    note = _resolve_or_die(notes, query)
    sessions = load_sessions(cfg)
    atoms = sorted(load_ledgers(cfg).get(note["slug"], []), key=lambda a: a.get("date", ""))
    print()
    print(c("◆ receipts", "blue", bold=True) + f" — {c(note['slug'], 'fg', bold=True)}")
    if not atoms:
        # legacy note with no ledger yet: fall back to frontmatter provenance
        print(c(f"  no atom ledger yet — note predates the ledger store; frontmatter sources:", "dim"))
        for s in note["sources"]:
            print(f"    {c('●', 'teal')} {c(session_label(s, sessions), 'fg')}")
        print()
        return
    srcs = {a.get("source", "") for a in atoms}
    print("  " + c(f"{len(atoms)} atoms from {len(srcs)} sessions — every claim carries its quote", "dim"))
    conf_color = {"high": "green", "medium": "yellow", "low": "red"}
    for a in atoms[:limit] if limit else atoms:
        print()
        mark = c("●", conf_color.get(a.get("confidence", ""), "gray"))
        for ln in wrap_ansi(f"  {mark} " + c(a.get("claim", ""), "fg"), W - 2, "    "):
            print(ln)
        ev = " ".join((a.get("evidence") or "").split())
        if ev:
            for ln in wrap_ansi("    " + c("“" + ev + "”", "gray", italic=True), W - 2, "     "):
                print(ln)
        print("    " + c(f"{a.get('date', '?')} · session {session_label(a.get('source'), sessions)}", "dim"))
    if limit and len(atoms) > limit:
        print()
        print("  " + c(f"… {len(atoms) - limit} more atoms (--limit 0 for all)", "dim"))
    print()


def conflicts(cfg, query=None, limit=12):
    W = term_width()
    notes = load_notes(cfg)
    if query:
        notes = [_resolve_or_die(notes, query)]
    entries = []
    for n in notes:
        for date, text in parse_conflicts(n["conflicts"]):
            entries.append((date, n, text))
    entries.sort(key=lambda e: e[0], reverse=True)
    print()
    print(c("⚡ conflicts", "yellow", bold=True)
          + c(f" — {len(entries)} superseded facts across {sum(1 for n in notes if n['conflicts'])} notes."
              f" The losing fact stays in the log.", "dim"))
    for date, n, text in entries[:limit] if limit else entries:
        print()
        print(f"  {c(date or 'earlier', 'yellow')}  {badge(n['type'])} {c(n['slug'], 'fg', bold=True)}")
        # full session uuids are noise at this altitude (`why` has real provenance) — shorten
        text = re.sub(r"\(?sources? ?:? ?[0-9a-f-]{36}[0-9a-f-, ]*\)?", "", text)
        text = re.sub(r"([0-9a-f]{8})-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                      r"\1", text)
        text = re.sub(r"(superseded by)", lambda m: c(m.group(1), "yellow"), " ".join(text.split()))
        for ln in wrap_ansi("    " + c(text, "gray"), W - 2, "    "):
            print(ln)
    if limit and len(entries) > limit:
        print()
        print("  " + c(f"… {len(entries) - limit} more (--limit 0 for all)", "dim"))
    print()


def log(cfg, limit=25):
    """Reverse-chronological ledger: what memory learned, when, into which note."""
    ledgers = load_ledgers(cfg)
    rows = []  # (placed_at, slug, [atoms])
    for slug, atoms in ledgers.items():
        by_day = {}
        for a in atoms:
            by_day.setdefault(a.get("placed_at") or a.get("date") or "?", []).append(a)
        for day, group in by_day.items():
            rows.append((day, slug, group))
    rows.sort(key=lambda r: r[0], reverse=True)
    W = term_width()
    print()
    print(c("◆ memory log", "blue", bold=True) + c(" — newest first", "dim"))
    last_day = None
    for day, slug, group in rows[:limit] if limit else rows:
        if day != last_day:
            print()
            print(f"  {c(day, 'fg', bold=True)}")
            last_day = day
        claim = " ".join(group[0].get("claim", "").split())
        line = (f"    {c('+' + str(len(group)), 'green')} {c(slug, 'cyan')} "
                + c("· " + claim, "dim"))
        print(line if vlen(line) <= W else line[:_cut(line, W - 1)] + c("…", "dim"))
    if limit and len(rows) > limit:
        print()
        print("  " + c(f"… {len(rows) - limit} more (--limit 0 for all)", "dim"))
    print()


def _cut(styled, visible_cols):
    """Index into a styled string that keeps `visible_cols` visible chars (never splits ANSI)."""
    n, i = 0, 0
    while i < len(styled) and n < visible_cols:
        m = _ANSI.match(styled, i)
        if m:
            i = m.end()
        else:
            i += 1
            n += 1
    return i
