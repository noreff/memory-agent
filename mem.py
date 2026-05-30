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
    n = sum(1 for _ in cfg.inbox.open()) if cfg.inbox.exists() else 0
    print(f"knowledge: {cfg.knowledge_dir}")
    print(f"state:     {cfg.state_dir}")
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
    a = sub.add_parser("adopt")
    a.add_argument("--force", action="store_true")
    e = sub.add_parser("eval")
    e.add_argument("mode", nargs="?", choices=["inject", "lookup", "recall", "all"], default="all")
    e.add_argument("--backend", default=None)
    args = p.parse_args()
    cfg = cfgmod.load()
    {"status": cmd_status, "capture": cmd_capture, "inject": cmd_inject,
     "refresh": cmd_refresh, "merge": cmd_merge, "adopt": cmd_adopt,
     "eval": cmd_eval}[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
