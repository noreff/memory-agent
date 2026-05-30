"""engine/merge — consolidation: route new atoms INTO existing notes, gate new topics, re-synthesize
ONLY touched notes, promote with backup. This replaces per-session note synthesis (which produced
one-atom stubs): notes are only ever (a) existing rich notes updated with new facts, or (b) new notes
born from >= newNoteThreshold atoms about one subject.

Frontmatter is assembled in CODE, never by the model — the model returns a body (plus a conflicts
channel), so double-frontmatter / invented-fields bugs are structurally impossible.

Artifacts (state/derived/merge/):
  task.json      prepare: unrouted atoms (with ids) + index + pending topics
  routing.json   per-atom verdicts: into:<slug> | new:<topic> | duplicate | discard
  plan.json      gated plan computed by finalize (single source of truth for promote)
  staged/        model output: <slug>.body.md (BODY ONLY) + <slug>.meta.json {conflicts, confidence}
  out/           finalize: fully assembled notes, ready for review/promote
  pending.json   atoms for topics below the threshold, accumulating across runs
  history/<ts>/  archived artifacts after promote

Nothing is marked consumed until PROMOTE — a discarded staging run leaves every atom unrouted, so the
pipeline is idempotent and re-runnable."""
from __future__ import annotations
import datetime
import hashlib
import json
import re
import shutil

CONFLICT_SENTINEL = "===CONFLICTS==="
NOTE_TYPES = {"user", "feedback", "project", "reference", "decision", "concept", "claim", "entity"}
FM_ORDER = ["type", "sources", "confidence", "links", "updated", "conflicts"]


# ── paths / small helpers ────────────────────────────────────────────────────
def merge_dir(cfg):
    return cfg.state_dir / "derived" / "merge"


def atoms_dir(cfg):
    return cfg.state_dir / "derived" / "atoms"


def _today():
    return datetime.date.today().isoformat()


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")[:60] or "topic"


def threshold(cfg) -> int:
    return int(cfg.merge_cfg.get("newNoteThreshold", 3))


def _read_json(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)  # atomic — concurrent readers never see a torn file


def _rubric(cfg, name):
    return (cfg.root / "core" / "prompts" / name).read_text(encoding="utf-8")


# ── atom store ───────────────────────────────────────────────────────────────
# Atom ids are CONTENT hashes ('file.json#<sha1(claim)[:10]>'), not positions: re-extracting a
# growing session reorders/overwrites its atom file, but the same claim keeps the same id, so
# routing coverage checks stay semantic and marking after re-extraction can't hit the wrong atom.
def atom_id(fname, atom):
    h = hashlib.sha1(str(atom.get("claim", "")).strip().lower().encode()).hexdigest()[:10]
    return f"{fname}#{h}"


def _iter_atom_files(cfg):
    d = atoms_dir(cfg)
    return sorted(d.glob("*.json")) if d.exists() else []


def collect_unrouted(cfg):
    out, seen = [], set()
    for f in _iter_atom_files(cfg):
        for a in _read_json(f, []):
            if isinstance(a, dict) and a.get("claim") and not a.get("routed"):
                aid = atom_id(f.name, a)
                if aid in seen:  # identical claim extracted twice → one routing entry
                    continue
                seen.add(aid)
                out.append({"id": aid, **a})
    return out


def count_unrouted(cfg) -> int:
    return len(collect_unrouted(cfg))


def atoms_by_id(cfg):
    out = {}
    for f in _iter_atom_files(cfg):
        for a in _read_json(f, []):
            if isinstance(a, dict):
                out[atom_id(f.name, a)] = a
    return out


def mark_atoms(cfg, ids, to):
    """Set atom['routed'] = {to, at} for each content-hash id. Marks every atom in the file whose
    claim hashes to the id (identical claims = the same fact). Idempotent."""
    by_file = {}
    for aid in ids:
        fname, _, h = aid.partition("#")
        by_file.setdefault(fname, set()).add(h)
    n = 0
    for fname, hashes in by_file.items():
        f = atoms_dir(cfg) / fname
        arr = _read_json(f, None)
        if arr is None:
            continue
        for a in arr:
            if isinstance(a, dict) and atom_id(fname, a).split("#")[1] in hashes:
                a["routed"] = {"to": to, "at": _today()}
                n += 1
        _write_json(f, arr)
    return n


# ── pending pool (topics below the new-note threshold) ───────────────────────
def load_pool(cfg):
    return _read_json(merge_dir(cfg) / "pending.json", {})


def save_pool(cfg, pool):
    _write_json(merge_dir(cfg) / "pending.json", pool)


# ── note frontmatter: parse + build (code-owned, model never writes it) ──────
def parse_note(text):
    """Return (fields, body). fields = ordered [(key, (kind, value))], kind in scalar|list|block."""
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.DOTALL)
    if not m:
        return [], text
    fields, body = [], m.group(2)
    lines = m.group(1).splitlines()
    i = 0
    while i < len(lines):
        km = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", lines[i])
        if not km:
            i += 1
            continue
        key, val = km.group(1), km.group(2).strip()
        if val == "|":
            i += 1
            block = []
            while i < len(lines) and (lines[i].startswith("  ") or not lines[i].strip()):
                block.append(lines[i][2:] if lines[i].startswith("  ") else "")
                i += 1
            fields.append((key, ("block", "\n".join(block).rstrip())))
        elif val.startswith("[") and val.endswith("]"):
            # flow-style list (backfill/hand-written notes use these) — never treat as a scalar
            items = [x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()]
            fields.append((key, ("list", items)))
            i += 1
        elif val == "":
            i += 1
            items = []
            while i < len(lines) and re.match(r"^\s+-\s+", lines[i]):
                items.append(re.sub(r"^\s+-\s+", "", lines[i]).strip())
                i += 1
            fields.append((key, ("list", items)))
        else:
            fields.append((key, ("scalar", val)))
            i += 1
    return fields, body


def fget(fields, key, default=None):
    for k, v in fields:
        if k == key:
            return v
    return default


def fset(fields, key, kind, value):
    for n, (k, _) in enumerate(fields):
        if k == key:
            fields[n] = (key, (kind, value))
            return fields
    fields.append((key, (kind, value)))
    return fields


def build_note(fields, body):
    fields = sorted(fields, key=lambda kv: (FM_ORDER.index(kv[0]) if kv[0] in FM_ORDER else 99))
    out = ["---"]
    for key, (kind, value) in fields:
        if kind == "scalar":
            out.append(f"{key}: {value}")
        elif kind == "list":
            if not value:
                out.append(f"{key}: []")
            else:
                out.append(f"{key}:")
                out += [f"  - {v}" for v in value]
        else:  # block
            if not str(value).strip():
                continue
            out.append(f"{key}: |")
            out += [f"  {ln}" for ln in str(value).splitlines()]
    out.append("---")
    return "\n".join(out) + "\n\n" + body.strip() + "\n"


def sanitize_body(text):
    """Defense in depth: strip fences and any model-written frontmatter block(s) from a body —
    but never a legitimate horizontal rule (only blocks whose lines look like `key: value`)."""
    t = text.strip()
    t = re.sub(r"^```[a-zA-Z]*\n", "", t)
    t = re.sub(r"\n```$", "", t).strip()
    while t.startswith("---"):
        m = re.match(r"^---\n(.*?)\n---\n?", t, re.DOTALL)
        if not m:
            break
        inner = [ln for ln in m.group(1).splitlines() if ln.strip()]
        kv = sum(1 for ln in inner
                 if re.match(r"^[A-Za-z_][\w-]*:", ln) or ln.startswith(("  ", "- ")))
        if not inner or kv / len(inner) < 0.6:
            break  # not frontmatter-shaped — leave the content alone
        t = t[m.end():].lstrip("\n")
    return t.strip()


def _append_conflicts(fields, new_text):
    new_text = (new_text or "").strip()
    if not new_text or new_text.lower() == "none":
        return fields
    cur = fget(fields, "conflicts")
    cur_text = (cur[1] if cur and cur[0] == "block" else (cur[1] if cur else "")) or ""
    cur_text = str(cur_text).strip()
    stamped = f"[{_today()}] {new_text}"
    return fset(fields, "conflicts", "block",
                f"{cur_text}\n\n{stamped}" if cur_text and cur_text != "[]" else stamped)


# ── index ────────────────────────────────────────────────────────────────────
def rebuild_index(kb):
    rows = []
    for note in sorted(kb.glob("*.md")):
        if note.name in ("index.md", "README.md"):
            continue
        fields, body = parse_note(note.read_text(encoding="utf-8", errors="ignore"))
        t = fget(fields, "type")
        summary = ""
        for ln in body.splitlines():
            s = ln.strip()
            if s and not s.startswith("#"):
                summary = s.lstrip("-* ").strip()[:140]
                break
        ttype = t[1] if t else "?"
        if ttype == "claim":  # time-sensitive snapshots: surface their age instead of aging silently
            upd = fget(fields, "updated")
            summary = f"(as of {upd[1] if upd else 'an unrecorded date'}) {summary}"[:160]
        rows.append((note.stem, ttype, summary.replace("|", "\\|")))
    lines = ["# Knowledge base — index", "",
             f"{len(rows)} canonical notes. Read this first; open a note for detail. Every note "
             "carries `sources` (provenance ids) and a `conflicts` field where facts disagreed.", "",
             "| Note | Type | Summary |", "|---|---|---|"]
    lines += [f"| [[{s}]] | {t} | {summ} |" for s, t, summ in rows]
    (kb / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(rows)


# ── stage: prepare ───────────────────────────────────────────────────────────
def valid_slugs(cfg):
    """Slugs of notes that actually exist — the ONLY legal 'into' targets."""
    if not cfg.knowledge_dir.exists():
        return set()
    return {p.stem for p in cfg.knowledge_dir.glob("*.md")} - {"index", "README"}


def normalize_decisions(decisions, valid):
    """Code-level guard: the route model may hallucinate 'into' targets (e.g. from its own injected
    memory). Any 'into' whose target is not an existing note becomes 'new:<target>' and flows through
    the gate like any other topic. Never trust the model on file existence."""
    out, demoted = [], 0
    for d in decisions:
        if d.get("verdict") == "into" and slugify(d.get("target", "")) not in valid:
            d = {**d, "verdict": "new", "topic": d.get("target") or "misc", "target": None}
            demoted += 1
        out.append(d)
    return out, demoted


def prepare(cfg):
    """Mechanical: collect unrouted atoms + index + pending topics into task.json. Clears any
    stale staging from an abandoned earlier run — a later promote must never ship old bodies."""
    md = merge_dir(cfg)
    for stale in ("staged", "out"):
        if (md / stale).exists():
            shutil.rmtree(md / stale)
    for stale in ("routing.json", "plan.json"):
        (md / stale).unlink(missing_ok=True)
    atoms = collect_unrouted(cfg)
    pool = load_pool(cfg)
    index_p = cfg.knowledge_dir / "index.md"
    task = {
        "atoms": atoms,
        "index": index_p.read_text(encoding="utf-8") if index_p.exists() else "(empty KB)",
        "validTargets": sorted(valid_slugs(cfg)),
        "pendingTopics": [{"topic": t, "type": v.get("type", "project"),
                           "count": len(v.get("atoms", []))} for t, v in pool.items()],
        "threshold": threshold(cfg),
        "knowledgeDir": str(cfg.knowledge_dir),
    }
    _write_json(md / "task.json", task)
    return {"taskPath": str(md / "task.json"), "atomCount": len(atoms),
            "validTargets": task["validTargets"],
            "pendingTopics": task["pendingTopics"], "threshold": task["threshold"]}


def check_staged(cfg):
    """Cheap-cycle guard: how many unrouted atoms are NOT already covered by the staged routing?
    The loop skips a re-merge when the staged batch already covers (almost) everything."""
    unrouted = {a["id"] for a in collect_unrouted(cfg)}
    routing = _read_json(merge_dir(cfg) / "routing.json", None) or {}
    staged = {d.get("id") for d in routing.get("decisions", []) if isinstance(d, dict)}
    out_dir = merge_dir(cfg) / "out"
    has_out = any(out_dir.glob("*.md")) if out_dir.exists() else False
    return {"unrouted": len(unrouted), "coveredByStaged": len(unrouted & staged),
            "newAtoms": len(unrouted - staged), "stagedOut": has_out}


# ── gate: deterministic plan from routing + pool ─────────────────────────────
def gate(decisions, pool, thresh):
    plan = {"into": {}, "new": {}, "duplicate": [], "discard": [], "pending_add": {}}
    by_topic = {}
    for d in decisions:
        v, aid = d.get("verdict"), d.get("id")
        if not aid:
            continue
        if v == "into" and d.get("target"):
            plan["into"].setdefault(slugify(d["target"]), []).append(d)
        elif v == "new" and d.get("topic"):
            by_topic.setdefault(d["topic"], []).append(d)
        elif v == "duplicate":
            plan["duplicate"].append(aid)
        elif v == "discard":
            plan["discard"].append(aid)
        # unknown/typo verdicts: leave the atom unrouted (model noise must not destroy data)
    for topic, ds in by_topic.items():
        pend = pool.get(topic, {})
        pend_atoms = pend.get("atoms", [])
        ty = next((d.get("type") for d in ds if d.get("type") in NOTE_TYPES), None) \
            or pend.get("type", "project")
        if len(ds) + len(pend_atoms) >= thresh:
            plan["new"][slugify(topic)] = {"topic": topic, "type": ty, "decisions": ds,
                                           "pending_atoms": pend_atoms}
        else:
            plan["pending_add"][topic] = {"type": ty, "decisions": ds}
    return plan


# ── stage: finalize (assemble notes; frontmatter built HERE, in code) ────────
def finalize(cfg, promote_flag=False, log=print):
    md = merge_dir(cfg)
    routing = _read_json(md / "routing.json", None)
    if not routing or not isinstance(routing.get("decisions"), list):
        log(json.dumps({"error": "no routing.json — run route first"}))
        return None
    decisions, demoted = normalize_decisions(routing["decisions"], valid_slugs(cfg))
    if demoted:
        log(f"  normalize: {demoted} 'into' decisions had nonexistent targets -> demoted to 'new'")
    plan = gate(decisions, load_pool(cfg), threshold(cfg))
    _write_json(md / "plan.json", plan)
    lookup = atoms_by_id(cfg)
    staged, out_dir = md / "staged", md / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    res = {"updated": [], "created": [], "missingBody": [], "duplicates": len(plan["duplicate"]),
           "discards": len(plan["discard"]),
           "pending": {t: len(v["decisions"]) for t, v in plan["pending_add"].items()}}

    def _meta(slug):
        return _read_json(staged / f"{slug}.meta.json", {})

    def _sources_of(decisions, extra_atoms=()):
        srcs = set()
        for d in decisions:
            a = lookup.get(d["id"], d)
            if a.get("source"):
                srcs.add(a["source"])
        for a in extra_atoms:
            if a.get("source"):
                srcs.add(a["source"])
        return sorted(srcs)

    for slug, ds in plan["into"].items():
        body_f = staged / f"{slug}.body.md"
        note_f = cfg.knowledge_dir / f"{slug}.md"
        if not body_f.exists():
            res["missingBody"].append(slug)
            continue
        if not note_f.exists():
            log(f"  warn: routed into missing note {slug} — skipping")
            res["missingBody"].append(slug)
            continue
        fields, _ = parse_note(note_f.read_text(encoding="utf-8"))
        cur = fget(fields, "sources")
        cur_list = [] if cur is None else (cur[1] if cur[0] == "list" else [str(cur[1])])
        merged_sources = sorted(set(cur_list) | set(_sources_of(ds)))
        fset(fields, "sources", "list", merged_sources)
        fset(fields, "updated", "scalar", _today())
        _append_conflicts(fields, _meta(slug).get("conflicts", ""))
        (out_dir / f"{slug}.md").write_text(
            build_note(fields, sanitize_body(body_f.read_text(encoding="utf-8"))), encoding="utf-8")
        res["updated"].append(slug)

    for slug, info in plan["new"].items():
        body_f = staged / f"{slug}.body.md"
        if not body_f.exists():
            res["missingBody"].append(slug)
            continue
        meta = _meta(slug)
        fields = [("type", ("scalar", info["type"])),
                  ("sources", ("list", _sources_of(info["decisions"], info["pending_atoms"]))),
                  ("confidence", ("scalar", str(meta.get("confidence", 0.8)))),
                  ("links", ("list", [])),
                  ("updated", ("scalar", _today()))]
        _append_conflicts(fields, meta.get("conflicts", ""))
        (out_dir / f"{slug}.md").write_text(
            build_note(fields, sanitize_body(body_f.read_text(encoding="utf-8"))), encoding="utf-8")
        res["created"].append(slug)

    if promote_flag and (res["updated"] or res["created"] or plan["duplicate"]
                         or plan["discard"] or plan["pending_add"]):
        res["promote"] = promote(cfg, plan, log=log)
    log(json.dumps(res, ensure_ascii=False))
    return res


# ── stage: promote (backup → write → reindex → consume atoms → archive) ─────
def promote(cfg, plan=None, log=print):
    md = merge_dir(cfg)
    if plan is None:
        plan = _read_json(md / "plan.json", None)
        if plan is None:
            log(json.dumps({"error": "no plan.json — run finalize first"}))
            return None
    out_dir = md / "out"
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = cfg.state_dir / "backups" / ts
    written, backed = [], 0
    cfg.knowledge_dir.mkdir(parents=True, exist_ok=True)
    for note in sorted(out_dir.glob("*.md")):
        dst = cfg.knowledge_dir / note.name
        if dst.exists():
            backup.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, backup / note.name)
            backed += 1
        shutil.copy2(note, dst)
        written.append(note.stem)
    n_index = rebuild_index(cfg.knowledge_dir)

    # consume atoms — only now do they leave the unrouted pool
    marked = 0
    for slug, ds in plan["into"].items():
        if slug in written:
            marked += mark_atoms(cfg, [d["id"] for d in ds], slug)
    pool = load_pool(cfg)
    for slug, info in plan["new"].items():
        if slug not in written:
            continue
        marked += mark_atoms(cfg, [d["id"] for d in info["decisions"]], slug)
        marked += mark_atoms(cfg, [a["id"] for a in info["pending_atoms"] if a.get("id")], slug)
        pool.pop(info["topic"], None)
    marked += mark_atoms(cfg, plan["duplicate"], "duplicate")
    marked += mark_atoms(cfg, plan["discard"], "discarded")
    lookup = atoms_by_id(cfg)
    for topic, info in plan["pending_add"].items():
        ids = [d["id"] for d in info["decisions"]]
        marked += mark_atoms(cfg, ids, f"pending:{topic}")
        entry = pool.setdefault(topic, {"type": info["type"], "atoms": [],
                                        "first_seen": _today()})
        known = {a.get("id") for a in entry["atoms"]}
        for aid in ids:
            if aid not in known and aid in lookup:
                entry["atoms"].append({"id": aid, **lookup[aid]})
    save_pool(cfg, pool)

    # archive run artifacts
    hist = md / "history" / ts
    hist.mkdir(parents=True, exist_ok=True)
    for name in ("task.json", "routing.json", "plan.json"):
        if (md / name).exists():
            shutil.move(str(md / name), hist / name)
    for sub in ("staged", "out"):
        if (md / sub).exists():
            shutil.move(str(md / sub), hist / sub)
    return {"written": written, "backedUp": backed, "backupDir": str(backup) if backed else None,
            "indexNotes": n_index, "atomsConsumed": marked}


def promote_staged(cfg, log=print):
    r = promote(cfg, None, log=log)
    if r is not None:
        log(json.dumps(r, ensure_ascii=False))
    return r


# ── completion-path drivers (workflow path uses agents + prepare/finalize) ───
def _parse_json_lenient(text):
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1)
    m = re.search(r"(\{.*\})", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def route_completion(cfg, backend, task):
    from adapters.model.base import Task
    from core.config import SENTINEL
    prompt = (f"{SENTINEL}\nINDEX:\n{task['index']}\n\nPENDING TOPICS: "
              f"{json.dumps(task['pendingTopics'])}\n\nATOMS:\n"
              f"{json.dumps(task['atoms'], ensure_ascii=False, indent=1)}")
    for _ in range(2):
        r = backend.run(Task(phase="route", system=_rubric(cfg, "route.md"), prompt=prompt,
                             max_tokens=8000, expect_json=True))
        obj = _parse_json_lenient(r.text)
        if obj and isinstance(obj.get("decisions"), list):
            return obj["decisions"]
    return None


def synth_completion(cfg, backend, plan, log=print):
    from adapters.model.base import Task
    staged = merge_dir(cfg) / "staged"
    staged.mkdir(parents=True, exist_ok=True)

    def _atoms_payload(decisions, extra=()):
        lookup = atoms_by_id(cfg)
        items = [lookup.get(d["id"], d) for d in decisions] + list(extra)
        return json.dumps([{k: a.get(k) for k in ("claim", "type", "entities", "evidence",
                                                  "source", "date")} for a in items],
                          ensure_ascii=False, indent=1)

    def _run(rubric, prompt, slug):
        from core.config import SENTINEL
        r = backend.run(Task(phase="merge", system=_rubric(cfg, rubric),
                             prompt=f"{SENTINEL}\n{prompt}", max_tokens=6000))
        body, _, conflicts = r.text.partition(CONFLICT_SENTINEL)
        (staged / f"{slug}.body.md").write_text(sanitize_body(body) + "\n", encoding="utf-8")
        _write_json(staged / f"{slug}.meta.json", {"conflicts": conflicts.strip() or "none"})
        log(f"  synth {slug}: {len(body)} chars")

    for slug, ds in plan["into"].items():
        note = (cfg.knowledge_dir / f"{slug}.md")
        if not note.exists():
            continue
        _run("merge-note.md",
             f"EXISTING NOTE ({slug}.md):\n{note.read_text(encoding='utf-8')}\n\n"
             f"NEW ATOMS:\n{_atoms_payload(ds)}", slug)
    for slug, info in plan["new"].items():
        _run("new-note.md",
             f"SUBJECT: {info['topic']} (type: {info['type']})\n\n"
             f"ATOMS:\n{_atoms_payload(info['decisions'], info['pending_atoms'])}", slug)


def run_all(cfg, backend_override=None, dry_run=False, promote=False, log=print):
    """Full completion-path pipeline: prepare → route → gate → synth → finalize [→ promote]."""
    from adapters.model.loader import backend_for_phase
    info = prepare(cfg)
    log(f"prepare: {info['atomCount']} unrouted atoms, "
        f"{len(info['pendingTopics'])} pending topics, threshold={info['threshold']}")
    if not info["atomCount"]:
        return
    task = _read_json(merge_dir(cfg) / "task.json", {})
    rb = backend_for_phase(cfg, "route", backend_override)
    log(f"route backend: {rb.name}")
    decisions = route_completion(cfg, rb, task)
    if decisions is None:
        log("route: model output unparseable after retry — aborting (nothing consumed)")
        return
    _write_json(merge_dir(cfg) / "routing.json", {"decisions": decisions})
    decisions, demoted = normalize_decisions(decisions, valid_slugs(cfg))
    if demoted:
        log(f"normalize: {demoted} 'into' decisions had nonexistent targets -> demoted to 'new'")
    plan = gate(decisions, load_pool(cfg), threshold(cfg))
    for d in decisions:
        tgt = d.get("target") or d.get("topic") or ""
        log(f"  {d.get('verdict', '?'):9} {tgt:42} {(d.get('claim') or d['id'])[:80]}")
    log(f"gate: into={len(plan['into'])} notes, new={list(plan['new'])}, "
        f"dup={len(plan['duplicate'])}, discard={len(plan['discard'])}, "
        f"pending+={list(plan['pending_add'])}")
    if dry_run:
        log("dry-run: stopping after route (routing.json written, nothing synthesized)")
        return
    sb = backend_for_phase(cfg, "merge", backend_override)
    log(f"synth backend: {sb.name}")
    synth_completion(cfg, sb, plan, log=log)
    finalize(cfg, promote_flag=promote or bool(cfg.merge_cfg.get("autoPromote")), log=log)
