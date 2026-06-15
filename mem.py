#!/usr/bin/env python3
"""memory-agent CLI — manual driver for the live layer (also the /loop entrypoint).

  python3 mem.py status              show KB/state paths, enabled adapters, inbox depth
  python3 mem.py capture            scan enabled adapters; enqueue new sessions (incremental)
  python3 mem.py capture --baseline record current transcripts as seen, enqueue nothing
  python3 mem.py inject [--cwd D]   print the SessionStart payload (debug)
  python3 mem.py refresh [opts]     drain inbox -> extract atoms into the atom store (atoms ONLY)
       --backend NAME   override the extract backend (local|cloud|subscription|stub)
       --limit N        process at most N pending sessions
       --dry-run        distill only; no model calls
  python3 mem.py eval [MODE]        regression scores: inject | recall | all (default all)
       --backend NAME   model backend to grade with (default: local)
  python3 mem.py merge [opts]       consolidate unrouted atoms into the KB (strong model)
       --stage S        all | prepare | finalize | promote   (default all; prepare/finalize are
                        the mechanical halves used by the merge Workflow)
       --backend NAME   override route+synth backend for --stage all
       --dry-run        route only: print the per-atom routing table, synthesize nothing
       --promote        promote assembled notes into knowledge/ (else stage for review)
  python3 mem.py cycle [opts]       ONE autonomous tick (the single launchd/cron/manual entrypoint):
                        capture -> local extract -> local merge+promote, gated by backlog. LOCAL-ONLY
                        by policy, so it is safe to run unattended; degrades to capture-only when no
                        local server is up. This is how memory stays fresh with no session, $0.
       --min-growth N   defer active sessions grown < N bytes (default: merge.cycleMinGrowth/75000)
       --min-atoms N    skip the merge below N unrouted atoms (default: merge.cycleMinAtoms/8)
  python3 mem.py sources            the registry of PLACES indexed (one KB, many sources):
                        (no arg) list every place + live stats, and where knowledge/state resolve to
       add <path>       register a folder (export / dump / docs) as a first-class incremental source
            --format F --id ID --backfill (label only) --ingest (enqueue now vs the safe baseline)
       remove <id>      unregister a place (state history is left intact)
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:  # stream progress even when stdout is a pipe (background runs, /loop, tee)
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

from core import config as cfgmod          # noqa: E402
from engine.capture import capture          # noqa: E402
from engine.inject import build_payload     # noqa: E402
from engine.refresh import refresh          # noqa: E402
from adapters.agent.loader import load_adapters   # noqa: E402
from adapters.model.loader import backend_for_phase  # noqa: E402


def cmd_status(cfg, args):
    from adapters.model.loader import detect_backend
    n = sum(1 for _ in cfg.inbox.open()) if cfg.inbox.exists() else 0
    print(f"knowledge: {cfg.knowledge_dir}")
    print(f"state:     {cfg.state_dir}")
    try:
        print(f"extract backend (auto): {detect_backend(cfg)}")
    except RuntimeError as e:
        print(f"extract backend (auto): NONE — {e}")
    print(f"inbox pending: {n}")
    from engine.merge import count_unrouted
    print(f"unrouted atoms: {count_unrouted(cfg)}")
    for a in load_adapters(cfg):
        exists = a.transcripts_dir.exists() if a.transcripts_dir else False
        print(f"  {a!r}  dir={a.transcripts_dir} (exists={exists})")


def cmd_capture(cfg, args):
    adapters = load_adapters(cfg)
    if not adapters:
        print("no enabled adapters")
        return
    for a in adapters:
        r = capture(a, cfg, baseline=args.baseline)
        print(f"[{r['adapter']}] scanned={r['scanned']} enqueued={r['enqueued']} "
              f"baseline={r['baseline']}")


def cmd_inject(cfg, args):
    print(build_payload(cfg, cwd=args.cwd))


def cmd_refresh(cfg, args):
    from engine.lock import Busy, pipeline_lock
    extract = backend_for_phase(cfg, "extract", args.backend)
    print(f"extract backend: {extract.name}")
    try:
        with pipeline_lock(cfg, "refresh"):
            r = refresh(cfg, extract, limit=args.limit, dry_run=args.dry_run,
                        min_growth=args.min_growth)
    except Busy as e:
        print(f"refresh: skipped — {e}")
        return
    print(f"refresh: sessions={r['sessions']} atoms={r['atoms']} (atom store only; "
          f"run merge to consolidate into the KB)")


def cmd_merge(cfg, args):
    from engine import merge as M
    from engine.lock import Busy, pipeline_lock
    if args.stage == "check":
        print(json.dumps(M.check_staged(cfg)))
        return
    try:
        if args.stage == "prepare":
            with pipeline_lock(cfg, "merge"):
                print(json.dumps(M.prepare(cfg)))
        elif args.stage == "finalize":
            with pipeline_lock(cfg, "merge"):
                M.finalize(cfg,
                           promote_flag=args.promote or bool(cfg.merge_cfg.get("autoPromote")))
        elif args.stage == "promote":
            with pipeline_lock(cfg, "merge"):
                M.promote_staged(cfg)
        else:
            with pipeline_lock(cfg, "merge"):
                M.run_all(cfg, backend_override=args.backend, dry_run=args.dry_run,
                          promote=args.promote)
    except Busy as e:
        print(json.dumps({"skipped": str(e)}))
    except RuntimeError as e:  # e.g. `claude` CLI absent for the subscription backend
        print(json.dumps({"error": str(e), "hint": "pick another backend: mem.py merge "
                          "--backend local|cloud, or install the missing CLI"}))


def cmd_cycle(cfg, args):
    """One autonomous tick — the single scheduler entrypoint (launchd / cron / manual):
    capture -> local extract -> local merge+promote, gated by backlog. LOCAL-ONLY by policy, so it
    is safe to run unattended in a daemon (it never redeems the subscription / a paid backend
    headlessly). Degrades cleanly: no local server up -> capture and queue, then return. This is the
    whole 'always fresh, no session, $0' loop in one verb."""
    from engine.lock import Busy, pipeline_lock
    from engine.merge import count_unrouted, run_all
    from adapters.model.loader import local_reachable, backend_for_phase
    min_growth = args.min_growth if args.min_growth is not None \
        else int(cfg.merge_cfg.get("cycleMinGrowth", 75000))
    min_atoms = args.min_atoms if args.min_atoms is not None \
        else int(cfg.merge_cfg.get("cycleMinAtoms", 8))

    # 1. capture — compute-free manifest diff; always runs
    adapters = load_adapters(cfg)
    if not adapters:
        print("cycle: no enabled adapters")
        return
    scanned = enqueued = 0
    for a in adapters:
        r = capture(a, cfg)
        scanned += r["scanned"]
        enqueued += r["enqueued"]
    print(f"capture: scanned={scanned} enqueued={enqueued}")

    # 2. extract — LOCAL ONLY. A daemon must not fall through the auto-chain to the subscription;
    #    no local server => leave the work queued for the next tick.
    if not local_reachable(cfg):
        print("cycle: local model server unreachable — captured only, atoms queued for next tick")
        return
    extract = backend_for_phase(cfg, "extract", "local")
    print(f"extract backend: {extract.name} ({getattr(extract, 'model', '?')})")
    try:
        with pipeline_lock(cfg, "refresh"):
            r = refresh(cfg, extract, min_growth=min_growth)
        print(f"refresh: sessions={r['sessions']} atoms={r['atoms']}")
    except Busy as e:
        print(f"cycle: refresh skipped — {e}")
        return

    # 3. merge + promote — gated: only spin the model for a full route/synth when the backlog is
    #    worth it; otherwise memory is already fresh. Batched (merge.routeBatch) — a large backlog
    #    drains across successive ticks rather than hogging the model for one long run.
    n = count_unrouted(cfg)
    if n < min_atoms:
        print(f"merge: {n} unrouted < {min_atoms} — fresh, nothing to consolidate")
        return
    print(f"merge: {n} unrouted >= {min_atoms} — consolidating on local")
    try:
        with pipeline_lock(cfg, "merge"):
            run_all(cfg, backend_override="local", promote=True)
    except Busy as e:
        print(f"cycle: merge skipped — {e}")


def cmd_sources(cfg, args):
    from engine import sources as S
    if args.action == "add":
        if not args.target:
            print("usage: mem.py sources add <path> [--format F] [--id ID] [--backfill] [--ingest]")
            return
        try:
            rec = S.add(cfg, args.target, fmt=args.format or "auto", id=args.id,
                        kind="backfill" if args.backfill else "generic")
        except ValueError as e:
            print(f"sources: {e}")
            return
        # Safe default: baseline the new place (record current files as seen, enqueue nothing) so a
        # large folder can't flood the inbox. --ingest enqueues everything for incremental extraction.
        from adapters.agent.generic import GenericAdapter
        a = GenericAdapter.from_config({"adapter": "generic", "name": rec["id"],
                                        "transcripts": {"dir": rec["path"], "format": rec["format"]}})
        r = capture(a, cfg, baseline=not args.ingest)
        print(f"registered [{rec['kind']}] {rec['id']} -> {rec['path']}")
        if args.ingest:
            print(f"  ingest: scanned={r['scanned']} enqueued={r['enqueued']} — "
                  f"run `mem.py cycle` to extract (or the backfill Workflow for bulk global-clustering)")
        else:
            print(f"  baselined {r['scanned']} existing files (not enqueued). For the initial bulk "
                  f"use the backfill Workflow; new files from now on capture incrementally.")
        return
    if args.action == "remove":
        if not args.target:
            print("usage: mem.py sources remove <id>")
            return
        if S.remove(cfg, args.target):
            print(f"removed source '{args.target}' (its manifest/atoms stay as harmless history)")
        else:
            print(f"no source with id '{args.target}'")
        return
    info = S.places(cfg)
    print(f"knowledge: {info['knowledge']}  ({info['notes']} notes)")
    print(f"state:     {info['state']}")
    print(f"sources ({len(info['sources'])}):")
    for s in info["sources"]:
        print(f"  [{s['kind']:8}] {s['id']:24} files={s['files']:<5} captured={s['captured']:<5} "
              f"atoms={s['atoms']:<6} last={s['last']:<11} {s['status']}")
        print(f"  {'':10} {s['path']}")


def cmd_adopt(cfg, args):
    """Backfill last mile: promote state/derived/notes/*.md into knowledge/ and build the index."""
    from engine.merge import rebuild_index
    src = cfg.state_dir / "derived" / "notes"
    notes = sorted(src.glob("*.md")) if src.exists() else []
    if not notes:
        print(f"nothing to adopt — no notes in {src} (run the backfill first)")
        return
    cfg.knowledge_dir.mkdir(parents=True, exist_ok=True)
    skipped = 0
    for n in notes:
        dst = cfg.knowledge_dir / n.name
        if dst.exists() and not args.force:
            skipped += 1
            continue
        dst.write_text(n.read_text(encoding="utf-8"), encoding="utf-8")
    total = rebuild_index(cfg.knowledge_dir)
    print(f"adopted {len(notes) - skipped} notes ({skipped} existing kept — use --force to "
          f"overwrite); index rebuilt: {total} notes in {cfg.knowledge_dir}")


def cmd_eval(cfg, args):
    from adapters.model.loader import build_backend
    from engine.evals import eval_inject, eval_lookup, eval_recall
    backend = build_backend(args.backend or "local", cfg)
    results = []
    if args.mode in ("inject", "all"):
        results.append(eval_inject(cfg, backend))
    if args.mode in ("lookup", "all"):
        results.append(eval_lookup(cfg, backend))
    if args.mode in ("recall", "all"):
        results.append(eval_recall(cfg, backend))
    results = [r for r in results if r]
    if results:  # append to score history so quality is a trend, not a memory
        import datetime
        hist = cfg.root / "eval" / "history.jsonl"
        hist.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        with hist.open("a", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps({"at": stamp, **r}) + "\n")
    print(json.dumps(results))


def main():
    p = argparse.ArgumentParser(prog="mem")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    c = sub.add_parser("capture")
    c.add_argument("--baseline", action="store_true")
    i = sub.add_parser("inject")
    i.add_argument("--cwd", default=None)
    r = sub.add_parser("refresh")
    r.add_argument("--backend", default=None)
    r.add_argument("--limit", type=int, default=None)
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--min-growth", type=int, default=0,
                   help="skip sessions whose transcript grew fewer bytes than this (stay queued)")
    m = sub.add_parser("merge")
    m.add_argument("--stage", choices=["all", "prepare", "finalize", "promote", "check"],
                   default="all")
    m.add_argument("--backend", default=None)
    m.add_argument("--dry-run", action="store_true")
    m.add_argument("--promote", action="store_true")
    sc = sub.add_parser("sources")
    sc.add_argument("action", nargs="?", choices=["list", "add", "remove"], default="list")
    sc.add_argument("target", nargs="?", default=None, help="folder path (add) or source id (remove)")
    sc.add_argument("--format", default=None)
    sc.add_argument("--id", default=None)
    sc.add_argument("--backfill", action="store_true", help="label the place as bulk-backfilled")
    sc.add_argument("--ingest", action="store_true",
                    help="enqueue current contents now (default: baseline, capture new files only)")
    cy = sub.add_parser("cycle")
    cy.add_argument("--min-growth", type=int, default=None,
                    help="defer active sessions grown fewer bytes than this "
                         "(default: merge.cycleMinGrowth or 75000)")
    cy.add_argument("--min-atoms", type=int, default=None,
                    help="skip the local merge below this many unrouted atoms "
                         "(default: merge.cycleMinAtoms or 8)")
    a = sub.add_parser("adopt")
    a.add_argument("--force", action="store_true")
    e = sub.add_parser("eval")
    e.add_argument("mode", nargs="?", choices=["inject", "lookup", "recall", "all"], default="all")
    e.add_argument("--backend", default=None)
    args = p.parse_args()
    cfg = cfgmod.load()
    {"status": cmd_status, "capture": cmd_capture, "inject": cmd_inject,
     "refresh": cmd_refresh, "merge": cmd_merge, "cycle": cmd_cycle, "sources": cmd_sources,
     "adopt": cmd_adopt, "eval": cmd_eval}[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
