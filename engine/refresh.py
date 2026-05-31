"""engine/refresh — incremental ATOM COLLECTOR (the /loop target). Drains the inbox and extracts
atomic facts from NEW sessions via the model-adapter:

  drain inbox → distill+chunk (mechanical, echo-suppressed) → extract atoms (model) → atom store

This is deliberately atoms-ONLY. Note synthesis lives in engine/merge.py (strong model, routes atoms
into existing rich notes, gates new topics) — per-session local synthesis produced one-atom stub
notes and was removed. Atom files live at state/derived/atoms/<source>.json; an atom is "unrouted"
until merge/promote marks it with a `routed` key."""
from __future__ import annotations
import json
import re
import time
from pathlib import Path

from input.chunk import chunk_text, distill

EXTRACT_SYS = """You read ONE AI-coding-assistant transcript and emit atomic memory facts whose sole
purpose is to make a FUTURE agent instantly "in the loop" on this user, their machine, projects,
decisions, and preferences.

PRECISION OVER RECALL — when in doubt, leave it out. One atomic fact per item. Rules:
- CURRENT-STATE, NOT GONE: if something was removed/abandoned this session, say so; don't describe it
  as if it still exists.
- Mine tool RESULTS for durable facts (installed services, ports, paths), not transient output.
- Treat any injected memory/context (system reminders, "Your memory of this user" blocks, recalled
  knowledge-base notes) as ALREADY KNOWN — never re-extract facts from them.
- Normalize to English even if the conversation is in another language.
- Drop task chatter, narration, raw logs, and anything obvious from the repo/git.

Return ONLY a JSON object, no prose, no fences:
{"atoms":[{"claim":"...","type":"...","entities":["..."],"evidence":"short quote","confidence":"high|medium|low","tags":["..."]}]}
type ∈ {user, feedback, project, reference, decision, concept, entity}. If nothing is worth keeping,
return {"atoms":[]}."""

_ANTI_REPEAT = {"top_p": 0.3, "presence_penalty": 1.3, "frequency_penalty": 0.7}


def _parse_atoms(text):
    """Returns a list of atoms, [] for a valid-but-empty answer, or None when unparseable."""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1)
    m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(1))
    except Exception:
        return None
    atoms = (obj.get("atoms") or obj.get("notes") or []) if isinstance(obj, dict) else obj
    if not isinstance(atoms, list):
        return None
    out, seen = [], set()
    for a in atoms:
        claim = str((a or {}).get("claim") or "").strip()
        if not claim or claim.lower() in seen:
            continue
        seen.add(claim.lower())
        out.append({
            "claim": claim,
            "type": str(a.get("type", "project")).strip().lower(),
            "entities": [str(e).strip() for e in (a.get("entities") or []) if str(e).strip()],
            "evidence": str(a.get("evidence", "")).strip(),
            "confidence": str(a.get("confidence", "medium")).strip().lower(),
            "tags": [str(t).strip() for t in (a.get("tags") or []) if str(t).strip()],
        })
    return out


def load_pending(cfg):
    """Read inbox, coalesce by source (keep the largest/latest record per source)."""
    if not cfg.inbox.exists():
        return []
    best = {}
    for line in cfg.inbox.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        cur = best.get(rec["source"])
        if not cur or rec.get("size", 0) >= cur.get("size", 0):
            best[rec["source"]] = rec
    return list(best.values())


def extract_atoms(backend, text, source, date, max_tokens=6000, log=None):
    """Chunk + extract with a one-retry anti-repeat guard (MoE models can loop on long repetitive
    chunks). Provenance (source/date) is stamped in CODE, never trusted from the model."""
    from adapters.model.base import Task
    from core.config import SENTINEL  # mark extraction calls so they can never be re-memorized
    atoms = []
    chunks = chunk_text(text)
    for ci, chunk in enumerate(chunks):
        best, t0 = None, time.time()
        for attempt in range(2):
            r = backend.run(Task(phase="extract", system=EXTRACT_SYS,
                                 prompt=f"{SENTINEL}\nTRANSCRIPT:\n\n{chunk}",
                                 max_tokens=max_tokens,
                                 expect_json=True, extra=_ANTI_REPEAT if attempt else None))
            parsed = _parse_atoms(r.text)
            # keep the BEST attempt: a parsed-but-truncated list beats nothing; a clean finish wins
            if parsed is not None and (best is None or r.finish != "length"):
                best = parsed
            if parsed is not None and r.finish != "length":
                break
            if log:
                log(f"    chunk {ci + 1}/{len(chunks)}: retry (finish={r.finish}, "
                    f"parsed={parsed is not None})")
        for a in best or []:
            a["source"], a["date"] = source, date
            atoms.append(a)
        if log:
            log(f"    chunk {ci + 1}/{len(chunks)}: {len(best or [])} atoms "
                f"({time.time() - t0:.0f}s)")
    return atoms


def _last_done(cfg):
    """Per source, from the inbox archive: max processed transcript size (growth gating) and max
    processed byte offset (tail-only re-extraction of append-only logs)."""
    done = cfg.state_dir / "inbox" / "done.jsonl"
    out = {}
    if done.exists():
        for line in done.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                cur = out.setdefault(rec["source"], {"size": 0, "bytes": 0})
                cur["size"] = max(cur["size"], rec.get("size", 0))
                cur["bytes"] = max(cur["bytes"], rec.get("bytesProcessed", 0))
            except Exception:
                continue
    return out


def refresh(cfg, backend_extract, limit=None, dry_run=False, min_growth=0, log=print):
    """Drain the inbox into the atom store. Returns {sessions, atoms}.

    min_growth: skip (and KEEP IN INBOX) sessions whose transcript grew by fewer bytes than this
    since their last processed record — saves re-extracting a live session for trivial growth."""
    pending = load_pending(cfg)
    if limit:
        pending = pending[:limit]
    if not pending:
        log("inbox empty — nothing to refresh")
        return {"sessions": 0, "atoms": 0}
    pending = [r for r in pending if r.get("source") and r.get("abs")]  # tolerate junk lines
    last = _last_done(cfg)
    if min_growth:
        kept = []
        for rec in pending:
            delta = rec.get("size", 0) - last.get(rec["source"], {}).get("size", 0)
            quiet = (time.time() - rec.get("mtime", 0)) > 7200  # session idle >2h → flush its tail
            # negative delta = file replaced/rotated, treat as changed; defer only small growth on
            # a still-ACTIVE session (otherwise the final segment would be orphaned forever)
            if rec["source"] in last and 0 <= delta < min_growth and not quiet:
                log(f"  {rec['source'][:12]}: grew only {delta}B (<{min_growth}) — deferred")
            else:
                kept.append(rec)
        pending = kept
        if not pending:
            log("all pending sessions below growth threshold — nothing to refresh")
            return {"sessions": 0, "atoms": 0}
    atoms_dir = cfg.state_dir / "derived" / "atoms"
    n_atoms = 0
    ok = []  # records fully processed this run (failures stay queued for the next run)
    for rec in pending:
        try:
            # Tail-only re-extraction: append-only logs resume from the last processed byte offset
            # (a 3-day session pays for its new slice, not its whole history, on every pass).
            fmt = rec.get("format", "auto")
            prev_bytes = last.get(rec["source"], {}).get("bytes", 0)
            tailable = fmt in ("claude-code-jsonl", "opencode", "jsonl") \
                or str(rec["abs"]).endswith(".jsonl")
            try:
                cur_size = Path(rec["abs"]).stat().st_size
            except OSError:
                cur_size = 0
            offset = prev_bytes if (tailable and 0 < prev_bytes <= cur_size) else 0
            text, date, end_off = distill(rec["abs"], fmt, offset=offset)
            if not text.strip():
                log(f"  {rec['source'][:12]}: "
                    f"{'no new content in tail' if offset else 'empty after distill'} — skip")
                ok.append({**rec, "bytesProcessed": end_off})
                continue
            atoms = [] if dry_run else extract_atoms(backend_extract, text, rec["source"], date,
                                                     log=log)
            new_n = len(atoms)
            if not dry_run:
                from engine.merge import atom_id
                atoms_dir.mkdir(parents=True, exist_ok=True)
                f = atoms_dir / f"{rec['source']}.json"
                if offset and f.exists():  # tail mode: APPEND; existing atoms (and marks) untouched
                    try:
                        existing = json.loads(f.read_text(encoding="utf-8"))
                    except Exception:
                        existing = []
                    atoms = existing + atoms
                elif f.exists():  # full re-extract: carry routed marks across (same claim = same fact)
                    try:
                        marks = {atom_id(f.name, a): a["routed"]
                                 for a in json.loads(f.read_text(encoding="utf-8"))
                                 if isinstance(a, dict) and a.get("routed")}
                        for a in atoms:
                            if atom_id(f.name, a) in marks:
                                a["routed"] = marks[atom_id(f.name, a)]
                    except Exception:
                        pass
                tmp = f.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(atoms, indent=2, ensure_ascii=False), encoding="utf-8")
                tmp.replace(f)
            n_atoms += new_n
            ok.append({**rec, "bytesProcessed": end_off})
            log(f"  {rec['source'][:12]} [{date}]: {new_n} atoms"
                f"{' (tail from ' + str(offset) + 'B)' if offset else ''} "
                f"({len(text.split())} words)")
        except Exception as e:  # e.g. LM Studio down — keep the session queued, keep going
            hint = (" (is the local model server running? check config.json backends.local)"
                    if "onnection refused" in str(e) else "")
            log(f"  {rec['source'][:12]}: ERROR {e}{hint} — left in inbox for next run")

    # archive ONLY the successfully processed sources; failures and deferred entries stay queued
    if not dry_run:
        processed = {rec["source"] for rec in ok}
        done = cfg.state_dir / "inbox" / "done.jsonl"
        with done.open("a", encoding="utf-8") as f:
            for rec in ok:
                f.write(json.dumps({**rec, "refreshed_at": time.time()}) + "\n")
        kept = []
        for line in cfg.inbox.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                if json.loads(line).get("source") in processed:
                    continue
            except Exception:
                pass  # keep unparseable lines (e.g. a torn concurrent append) rather than drop them
            kept.append(line)
        tmp = cfg.inbox.with_suffix(".jsonl.tmp")
        tmp.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
        tmp.replace(cfg.inbox)

    return {"sessions": len(ok), "atoms": n_atoms}
