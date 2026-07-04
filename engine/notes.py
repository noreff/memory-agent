"""engine/notes — the compiled-notes store: ATOMS are the source of truth, note prose is a
RENDERED VIEW (a compiled artifact).

The old model folded new atoms into existing prose (`synth(old prose + new atoms) -> new prose`) on
every touch: order-dependent, paraphrase-lossy, O(rewrites x note-size) model tokens, and the KB
could never be re-rendered by a better model. This module inverts that:

  - Each note owns a LEDGER (state/derived/notes/<slug>.atoms.jsonl): every atom ever placed into
    it, appended by CODE — instant, durable, zero model tokens.
  - A legacy note's current prose is frozen ONCE as a SEED (<slug>.seed.md) on first touch; from
    then on the note is a pure function: render(seed, ledger) -> prose. Idempotent, order-free,
    reproducible — and the whole KB can be re-rendered when a better model ships.
  - Between renders, placed atoms are visible immediately in a `## Recent (unconsolidated)`
    section that append() maintains in the note file — memory is fresh in seconds; polish is
    deferred to compaction (pending >= merge.compactEvery, or a cycle-end sweep).

Frontmatter stays code-owned (reuses engine.merge builders). Renders back up the previous file to
state/backups/ like promote always did.
"""
from __future__ import annotations
import datetime
import hashlib
import json
import re
import shutil

from engine.merge import (CONFLICT_SENTINEL, NOTE_TYPES, UNTRUSTED_FENCE, _append_conflicts,
                          _read_json, _rubric, build_note, fget, fset, parse_note, sanitize_body)

RECENT_HEADER = "## Recent (unconsolidated)"


# ── paths ────────────────────────────────────────────────────────────────────
def notes_state_dir(cfg):
    # 'ledgers', not 'notes' — state/derived/notes/ is the legacy backfill-workflow staging dir
    # that `mem.py adopt` globs for *.md; sharing it would let adopt swallow .seed.md files.
    space = getattr(cfg, "space", None)
    name = "ledgers" if space in (None, "default") else f"ledgers-{space}"
    return cfg.state_dir / "derived" / name


def ledger_path(cfg, slug):
    return notes_state_dir(cfg) / f"{slug}.atoms.jsonl"


def seed_path(cfg, slug):
    return notes_state_dir(cfg) / f"{slug}.seed.md"


def meta_path(cfg, slug):
    return notes_state_dir(cfg) / f"{slug}.meta.json"


def _today():
    return datetime.date.today().isoformat()


def _claim_hash(atom):
    # normalize to alnum before hashing: "root is /x/." and "root is /x/" are the SAME fact —
    # punctuation/spacing variants must not stack up as ledger near-duplicates
    norm = re.sub(r"[^a-z0-9]+", " ", str(atom.get("claim", "")).lower()).strip()
    return hashlib.sha1(norm.encode()).hexdigest()[:10]


# ── ledger ───────────────────────────────────────────────────────────────────
def load_ledger(cfg, slug):
    p = ledger_path(cfg, slug)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue  # a torn concurrent line must not take the whole ledger down
    return out


def pending_count(cfg, slug):
    total = len(load_ledger(cfg, slug))
    meta = _read_json(meta_path(cfg, slug), {})
    return max(0, total - int(meta.get("rendered", 0)))


def _ensure_seed(cfg, slug):
    """Freeze a legacy note's current prose as the seed, once. After this, the note is a pure
    function of (seed, ledger) — its live prose never feeds back into itself again."""
    sp = seed_path(cfg, slug)
    note = cfg.knowledge_dir / f"{slug}.md"
    if sp.exists() or not note.exists():
        return
    _, body = parse_note(note.read_text(encoding="utf-8"))
    body = _strip_recent(body).strip()
    sp.parent.mkdir(parents=True, exist_ok=True)
    tmp = sp.with_suffix(".md.tmp")
    tmp.write_text(body + "\n", encoding="utf-8")
    tmp.replace(sp)


def _strip_recent(body):
    idx = body.find(RECENT_HEADER)
    return body if idx < 0 else body[:idx].rstrip() + "\n"


def append_atoms(cfg, slug, atoms, note_type=None, log=print):
    """Place atoms into a note: append to its ledger + surface them in the note's Recent section.
    Pure code — instant and durable; the model is NOT on this path. Creates a skeleton note when
    the slug is new. Identical claims already in the ledger are skipped (content-hash dedup)."""
    if not atoms:
        return 0
    _ensure_seed(cfg, slug)
    lp = ledger_path(cfg, slug)
    lp.parent.mkdir(parents=True, exist_ok=True)
    known = {_claim_hash(a) for a in load_ledger(cfg, slug)}
    fresh = []
    for a in atoms:
        h = _claim_hash(a)
        if a.get("claim") and h not in known:
            known.add(h)
            fresh.append({**a, "placed_at": _today()})
    if not fresh:
        return 0
    with lp.open("a", encoding="utf-8") as f:
        for a in fresh:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")

    note = cfg.knowledge_dir / f"{slug}.md"
    cfg.knowledge_dir.mkdir(parents=True, exist_ok=True)
    if note.exists():
        fields, body = parse_note(note.read_text(encoding="utf-8"))
    else:
        ty = note_type if note_type in NOTE_TYPES else _majority_type(fresh)
        fields = [("type", ("scalar", ty)), ("sources", ("list", [])),
                  ("confidence", ("scalar", "0.8")), ("links", ("list", [])),
                  ("updated", ("scalar", _today()))]
        body = "_(new note — awaiting first compilation from its atoms)_\n"
    cur = fget(fields, "sources")
    cur_list = [] if cur is None else (cur[1] if cur[0] == "list" else [str(cur[1])])
    merged = sorted(set(cur_list) | {a["source"] for a in fresh if a.get("source")})
    fset(fields, "sources", "list", merged)
    fset(fields, "updated", "scalar", _today())
    base = _strip_recent(body).rstrip()
    recent = [f"- [{a.get('date') or a['placed_at']}] {a['claim']}" for a in fresh]
    prev = _recent_lines(body)
    body = base + f"\n\n{RECENT_HEADER}\n" + "\n".join(prev + recent) + "\n"
    tmp = note.with_suffix(".md.tmp")
    tmp.write_text(build_note(fields, body), encoding="utf-8")
    tmp.replace(note)
    return len(fresh)


def _recent_lines(body):
    idx = body.find(RECENT_HEADER)
    if idx < 0:
        return []
    return [ln for ln in body[idx + len(RECENT_HEADER):].splitlines() if ln.strip().startswith("-")]


def _majority_type(atoms):
    counts = {}
    for a in atoms:
        t = a.get("type")
        if t in NOTE_TYPES:
            counts[t] = counts.get(t, 0) + 1
    return max(counts, key=counts.get) if counts else "project"


# ── render (compaction): the ONLY model call in the write path, and it's rare ─
def render_note(cfg, backend, slug, log=print):
    """Compile prose from (seed, full ledger) — a pure function of durable inputs. The current
    live body is deliberately NOT an input: paraphrase drift can't accumulate, and re-rendering
    with a better model later reproduces the whole note from truth."""
    from adapters.model.base import Task
    from core.config import SENTINEL
    ledger = load_ledger(cfg, slug)
    note = cfg.knowledge_dir / f"{slug}.md"
    if not ledger and not note.exists():
        return False
    seed = seed_path(cfg, slug).read_text(encoding="utf-8") if seed_path(cfg, slug).exists() else ""
    cap = int(cfg.merge_cfg.get("synthNoteCharCap", 48000))
    # bound the ATOM payload too (a 270-atom ledger overflows the context and 400s the render):
    # keep the NEWEST atoms within budget — older ones are already reflected in the seed prose.
    atoms = sorted(ledger, key=lambda a: str(a.get("date") or ""))
    slim = [{k: a.get(k) for k in ("claim", "type", "entities", "evidence", "source", "date")}
            for a in atoms]
    used, kept = 0, []
    for a in reversed(slim):
        j = len(json.dumps(a, ensure_ascii=False))
        if used + j > cap and kept:
            break
        used += j
        kept.append(a)
    if len(kept) < len(slim):
        log(f"  render {slug}: ledger payload capped to newest {len(kept)}/{len(slim)} atoms")
    kept.reverse()
    payload = UNTRUSTED_FENCE.format(payload=json.dumps(kept, ensure_ascii=False, indent=1))
    if len(seed) > cap:  # pathological: keep head+tail so the render still fits the context
        seed = seed[:cap * 3 // 4] + "\n\n…[seed truncated]…\n\n" + seed[-cap // 4:]
    if seed:
        rubric, prompt = "merge-note.md", (f"EXISTING NOTE ({slug}.md):\n{seed}\n\n"
                                           f"NEW ATOMS:\n{payload}")
    else:
        topic = slug.replace("-", " ")
        rubric, prompt = "new-note.md", (f"SUBJECT: {topic} (type: {_majority_type(ledger)})\n\n"
                                         f"ATOMS:\n{payload}")
    try:
        # generous output budget: a reasoning model thinks 5-6k tokens BEFORE the body — with only
        # 8k total a big render starves and emits a stub (observed: 352 chars from 443 atoms)
        r = backend.run(Task(phase="merge", system=_rubric(cfg, rubric),
                             prompt=f"{SENTINEL}\n{prompt}",
                             max_tokens=int(cfg.merge_cfg.get("renderMaxTokens", 14000))))
    except Exception as e:  # one note must never crash the cycle
        log(f"  render {slug}: SKIPPED — {type(e).__name__}: {str(e)[:80]} (atoms stay in ledger)")
        return False
    body, _, conflicts = r.text.partition(CONFLICT_SENTINEL)
    body = sanitize_body(body)
    if len(body) < 40:  # a render that lost the note's content must not replace it
        log(f"  render {slug}: SKIPPED — degenerate body ({len(body)} chars)")
        return False

    if note.exists():
        fields, _ = parse_note(note.read_text(encoding="utf-8"))
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = cfg.state_dir / "backups" / ts
        backup.mkdir(parents=True, exist_ok=True)
        shutil.copy2(note, backup / note.name)
    else:
        fields = [("type", ("scalar", _majority_type(ledger))),
                  ("sources", ("list", [])), ("confidence", ("scalar", "0.8")),
                  ("links", ("list", [])), ("updated", ("scalar", _today()))]
    cur = fget(fields, "sources")
    cur_list = [] if cur is None else (cur[1] if cur[0] == "list" else [str(cur[1])])
    merged = sorted(set(cur_list) | {a["source"] for a in ledger if a.get("source")})
    fset(fields, "sources", "list", merged)
    fset(fields, "updated", "scalar", _today())
    _append_conflicts(fields, conflicts.strip())
    tmp = note.with_suffix(".md.tmp")
    tmp.write_text(build_note(fields, body), encoding="utf-8")
    tmp.replace(note)
    meta = _read_json(meta_path(cfg, slug), {})
    meta["rendered"] = len(ledger)
    meta["rendered_at"] = _today()
    mp = meta_path(cfg, slug)
    mp.parent.mkdir(parents=True, exist_ok=True)
    tmpm = mp.with_suffix(".json.tmp")
    tmpm.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    tmpm.replace(mp)
    log(f"  render {slug}: {len(body)} chars from {len(ledger)} atoms"
        + (" (+seed)" if seed else ""))
    return True


def compact(cfg, backend, slugs=None, force=False, log=print):
    """Render every note whose pending atoms crossed merge.compactEvery (or all pending if force).
    Runs renders concurrently (merge.synthConcurrency) — this is the amortized, off-the-write-path
    model work."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    every = int(cfg.merge_cfg.get("compactEvery", 8))
    if slugs is None:
        d = notes_state_dir(cfg)
        slugs = [p.name[:-len(".atoms.jsonl")] for p in d.glob("*.atoms.jsonl")] if d.exists() else []
    due = []
    for s in sorted(set(slugs)):
        pend = pending_count(cfg, s)
        if not pend:
            continue
        # a note born from atoms (no seed = no prior prose) needs its first body NOW; a legacy
        # note already reads fine (prose + Recent section) and waits for the threshold/sweep.
        newborn = (not seed_path(cfg, s).exists()
                   and not _read_json(meta_path(cfg, s), {}).get("rendered"))
        if force or pend >= every or newborn:
            due.append(s)
    if not due:
        return []
    workers = max(1, int(cfg.merge_cfg.get("synthConcurrency", 4)))
    log(f"  compact: {len(due)} note(s) due on {workers} worker(s)")
    done = []
    if workers == 1:
        for s in due:
            if render_note(cfg, backend, s, log=log):
                done.append(s)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(render_note, cfg, backend, s, log): s for s in due}
            for fut in as_completed(futs):
                if fut.result():
                    done.append(futs[fut])
    return done
