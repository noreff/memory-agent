"""engine/ingest — subject-centric consolidation that replaces the monolithic route batch.

The old `route` asked ONE LLM call for a large structured verdict over a big atom batch. On local
reasoning models that overflows: they "think" for thousands of tokens and never finish the JSON, so
the whole batch fails atomically. `ingest` reshapes the work into the form local models handle:

  1. PLACE — classify atoms against the index in SMALL safe chunks (`merge.routeBatch`, default 10 —
     empirically the only size whose thinking+JSON reliably fits), accumulating decisions across
     chunks. A chunk that fails to parse just leaves its atoms unrouted; the rest proceed.
  2. SYNTH — write ONE note per subject (the per-note, prose-shaped call local models do well).

One ingest run drains the whole backlog. Frontmatter is built in code and promote is reused, both
from engine.merge — so this module is a thin re-orchestration, not a reimplementation.
"""
from __future__ import annotations
import shutil

from engine import merge as M


def place_all(cfg, backend, atoms, log=print):
    """Route atoms in small chunks against the index; return the merged decisions list."""
    chunk_n = max(1, int(cfg.merge_cfg.get("routeBatch", 10)))
    index_p = cfg.knowledge_dir / "index.md"
    index_text = index_p.read_text(encoding="utf-8") if index_p.exists() else "(empty KB)"
    pool = M.load_pool(cfg)
    pending = [{"topic": t, "type": v.get("type", "project"), "count": len(v.get("atoms", []))}
               for t, v in pool.items()]
    decisions = []
    total = (len(atoms) + chunk_n - 1) // chunk_n
    for ci in range(total):
        chunk = atoms[ci * chunk_n:(ci + 1) * chunk_n]
        task = {"atoms": chunk, "index": index_text, "pendingTopics": pending}
        got = M.route_completion(cfg, backend, task)
        if not got:
            log(f"  place {ci + 1}/{total}: unparseable — {len(chunk)} atoms left unrouted")
            continue
        decisions.extend(got)
        log(f"  place {ci + 1}/{total}: +{len(got)} placed ({len(decisions)} total)")
    return decisions


def ingest(cfg, place_backend, synth_backend, limit=None, promote=True, log=print):
    """Drain unrouted atoms: place in small chunks → gate → synth one note per subject → promote."""
    atoms = M.collect_unrouted(cfg)
    if limit:
        atoms = atoms[:limit]
    if not atoms:
        log("ingest: nothing unrouted")
        return {"placed": 0, "into": 0, "new": 0}
    log(f"ingest: {len(atoms)} unrouted; place on {place_backend.name}, synth on {synth_backend.name}")

    md = M.merge_dir(cfg)
    for stale in ("staged", "out"):
        if (md / stale).exists():
            shutil.rmtree(md / stale)
    for stale in ("routing.json", "plan.json"):
        (md / stale).unlink(missing_ok=True)

    decisions = place_all(cfg, place_backend, atoms, log=log)
    if not decisions:
        log("ingest: no decisions parsed — nothing consumed")
        return {"placed": 0, "into": 0, "new": 0}
    M._write_json(md / "routing.json", {"decisions": decisions})

    decisions, demoted = M.normalize_decisions(decisions, M.valid_slugs(cfg))
    if demoted:
        log(f"  normalize: {demoted} invalid 'into' targets -> 'new'")
    plan = M.gate(decisions, M.load_pool(cfg), M.threshold(cfg))
    log(f"gate: into={len(plan['into'])} new={list(plan['new'])} dup={len(plan['duplicate'])} "
        f"discard={len(plan['discard'])} pending+={list(plan['pending_add'])}")

    # SYNTH is the heavy, reliable part — one prose note per subject (reused from engine.merge).
    M.synth_completion(cfg, synth_backend, plan, log=log)
    # finalize re-derives the plan from routing.json (same result), assembles notes with code-built
    # frontmatter, and (promote) backs up + writes into knowledge/ + reindexes + consumes atoms.
    M.finalize(cfg, promote_flag=promote, log=log)
    return {"placed": len(decisions), "into": len(plan["into"]), "new": len(plan["new"])}
