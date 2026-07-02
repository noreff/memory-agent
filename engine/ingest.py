"""engine/ingest — consolidation under the compiled-notes model (see engine/notes.py).

Placement is DETERMINISTIC-FIRST: an atom whose entities unambiguously name exactly one existing
note (or pending topic) is routed by CODE — instant, free, testable. Only the ambiguous residue
goes to the model, in small parallel chunks (`merge.routeBatch` — empirically the only size whose
thinking+JSON reliably fits a local reasoning model).

Applying a placement is an APPEND to the note's atom ledger (durable, code-only, zero tokens) —
the model is off the write path entirely. Prose is recompiled later by notes.compact() when a
note accumulates enough pending atoms. So one ingest step costs: N cheap routes + K appends;
the expensive synth work is amortized across many steps instead of paid on every touch.
"""
from __future__ import annotations
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from engine import merge as M
from engine import notes as N


# ── deterministic routing: entities -> slug, in code ────────────────────────
def _norm(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def _slug_matches(entity, slugs):
    """Slugs the normalized entity points at: exact slug, or a full hyphen-boundary substring
    (entity 'data-connectors' matches slug 'vana-data-connectors'; 'vana' matches too many and
    is discarded by the uniqueness rule in route_deterministic)."""
    e = _norm(entity)
    if not e or len(e) < 4:  # 1-3 char tokens ('pi', 'x') match everything — never trust them
        return set()
    hits = set()
    for s in slugs:
        if e == s or f"-{e}-" in f"-{s}-":
            hits.add(s)
    return hits


def route_deterministic(cfg, atoms, pool):
    """Split atoms into (decisions, residue): a decision is emitted only when the atom's entities
    collectively point at EXACTLY ONE existing note (-> into) or pending topic (-> new). Anything
    ambiguous or unmatched is residue for the model. Zero-false-positive by construction: one
    candidate or nothing."""
    slugs = M.valid_slugs(cfg)
    topics = {M.slugify(t): t for t in pool}
    decisions, residue = [], []
    for a in atoms:
        cands, topic_cands = set(), set()
        for e in (a.get("entities") or []):
            cands |= _slug_matches(e, slugs)
            topic_cands |= _slug_matches(e, topics.keys())
        if len(cands) == 1:
            decisions.append({"id": a["id"], "verdict": "into", "target": next(iter(cands))})
        elif not cands and len(topic_cands) == 1:
            decisions.append({"id": a["id"], "verdict": "new",
                              "topic": topics[next(iter(topic_cands))], "type": a.get("type")})
        else:
            residue.append(a)
    return decisions, residue


# ── model routing for the residue (small parallel chunks) ────────────────────
def place_all(cfg, backend, atoms, log=print):
    """Route atoms in small chunks against the index; return the merged decisions list.
    Chunks are independent — fan out to the local server's parallel slots
    (`merge.placeConcurrency`; 1 forces the old sequential behavior)."""
    chunk_n = max(1, int(cfg.merge_cfg.get("routeBatch", 10)))
    workers = max(1, int(cfg.merge_cfg.get("placeConcurrency", 4)))
    index_p = cfg.knowledge_dir / "index.md"
    index_text = index_p.read_text(encoding="utf-8") if index_p.exists() else "(empty KB)"
    pool = M.load_pool(cfg)
    pending = [{"topic": t, "type": v.get("type", "project"), "count": len(v.get("atoms", []))}
               for t, v in pool.items()]
    chunks = [atoms[i:i + chunk_n] for i in range(0, len(atoms), chunk_n)]
    total = len(chunks)

    def _place(chunk):
        task = {"atoms": chunk, "index": index_text, "pendingTopics": pending}
        return len(chunk), M.route_completion(cfg, backend, task)

    decisions, done = [], 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in as_completed(ex.submit(_place, c) for c in chunks):
            n, got = fut.result()
            done += 1
            if not got:
                log(f"  place {done}/{total}: unparseable — {n} atoms left unrouted")
                continue
            decisions.extend(got)
            log(f"  place {done}/{total}: +{len(got)} placed ({len(decisions)} total)")
    return decisions


# ── apply: placements become ledger APPENDS (durable, code-only) ─────────────
def _apply_plan(cfg, plan, log=print):
    lookup = M.atoms_by_id(cfg)

    def _full(ds, extra=()):
        return [{"id": d["id"], **lookup.get(d["id"], d)} for d in ds] + list(extra)

    touched = []
    for slug, ds in plan["into"].items():
        n = N.append_atoms(cfg, slug, _full(ds), log=log)
        M.mark_atoms(cfg, [d["id"] for d in ds], slug)
        touched.append(slug)
        log(f"  append {slug}: +{n} atoms (pending {N.pending_count(cfg, slug)})")
    pool = M.load_pool(cfg)
    for slug, info in plan["new"].items():
        atoms = _full(info["decisions"], info["pending_atoms"])
        n = N.append_atoms(cfg, slug, atoms, note_type=info["type"], log=log)
        M.mark_atoms(cfg, [d["id"] for d in info["decisions"]], slug)
        M.mark_atoms(cfg, [a["id"] for a in info["pending_atoms"] if a.get("id")], slug)
        pool.pop(info["topic"], None)
        touched.append(slug)
        log(f"  new {slug}: {n} atoms")
    M.mark_atoms(cfg, plan["duplicate"], "duplicate")
    M.mark_atoms(cfg, plan["discard"], "discarded")
    for topic, info in plan["pending_add"].items():
        ids = [d["id"] for d in info["decisions"]]
        M.mark_atoms(cfg, ids, f"pending:{topic}")
        entry = pool.setdefault(topic, {"type": info["type"], "atoms": [],
                                        "first_seen": N._today()})
        known = {a.get("id") for a in entry["atoms"]}
        for aid in ids:
            if aid not in known and aid in lookup:
                entry["atoms"].append({"id": aid, **lookup[aid]})
    M.save_pool(cfg, pool)
    return touched


def _spaces_of(cfg, atoms):
    groups = {}
    for a in atoms:
        groups.setdefault(a.get("space") or "default", []).append(a)
    return groups


def ingest(cfg, place_backend, synth_backend, limit=None, promote=True, log=print):
    """Drain unrouted atoms: deterministic route -> model route for residue -> ledger appends ->
    compact (render) the notes that crossed the threshold -> reindex + inject. Grouped per space."""
    from core.config import for_space
    from engine.inject import write_inject_files
    atoms = M.collect_unrouted(cfg)
    if limit:
        atoms = atoms[:limit]
    if not atoms:
        log("ingest: nothing unrouted")
        return {"placed": 0, "into": 0, "new": 0}

    total = {"placed": 0, "into": 0, "new": 0}
    for space, batch in sorted(_spaces_of(cfg, atoms).items()):
        scfg = for_space(cfg, space)
        pool = M.load_pool(scfg)
        det, residue = route_deterministic(scfg, batch, pool)
        log(f"ingest[{space}]: {len(batch)} atoms — {len(det)} routed in code, "
            f"{len(residue)} to the model")
        decisions = list(det)
        if residue:
            decisions += place_all(scfg, place_backend, residue, log=log) or []
        if not decisions:
            log(f"ingest[{space}]: no decisions — nothing consumed")
            continue
        decisions, demoted = M.normalize_decisions(decisions, M.valid_slugs(scfg))
        if demoted:
            log(f"  normalize: {demoted} invalid 'into' targets -> 'new'")
        plan = M.gate(decisions, pool, M.threshold(scfg))
        log(f"gate[{space}]: into={len(plan['into'])} new={list(plan['new'])} "
            f"dup={len(plan['duplicate'])} discard={len(plan['discard'])} "
            f"pending+={list(plan['pending_add'])}")
        touched = _apply_plan(scfg, plan, log=log)
        if promote:
            N.compact(scfg, synth_backend, slugs=touched, log=log)
            M.rebuild_index(scfg.knowledge_dir)
            write_inject_files(scfg)
        total["placed"] += len(decisions)
        total["into"] += len(plan["into"])
        total["new"] += len(plan["new"])
    return total


def sweep(cfg, synth_backend, log=print):
    """Cycle-end polish: force-render EVERY note with pending atoms (across spaces), so a drained
    backlog ends with zero unconsolidated sections. Between cycles this is a no-op."""
    from core.config import for_space, spaces
    from engine.inject import write_inject_files
    for space in spaces(cfg):
        scfg = for_space(cfg, space)
        done = N.compact(scfg, synth_backend, force=True, log=log)
        if done:
            M.rebuild_index(scfg.knowledge_dir)
            write_inject_files(scfg)
            log(f"sweep[{space}]: rendered {len(done)} note(s)")
