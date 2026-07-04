"""engine/garden — the KB's immune system against entropy (the 'lint' op the pipeline lacked).

Consolidation only ever ADDS notes; nothing un-fragments the taxonomy, so stubs and near-duplicate
slug families accumulate (observed live: workspace-root at 201B; data-pipe / data-pipe-api /
data-pipe-project). The gardener:

  1. finds STUB notes (< merge.gardenStubBytes, default 600B body) and asks the route model to
     pick a merge target from the index (verdict 'into') or keep them ('new'/'discard' -> keep);
  2. merges: the stub's ledger atoms move to the target's ledger, its prose becomes one synthetic
     ledger entry (nothing is lost), the target re-renders, the stub is deleted (with backup) and
     every [[stub]] link across the KB is rewritten to [[target]];
  3. JUDGES near-duplicate slug FAMILIES, a bounded number per run: a dedicated rubric decides
     "one fragmented topic" vs "legitimately different topics that share a prefix" — merging only
     on a confident 'same' (canonical member absorbs the rest), and remembering 'different'
     verdicts in state/derived/garden*.json so a kept family is never re-asked until its
     membership changes.

Conservative by design: merges capped per run (merge.gardenMaxMerges, default 8), families judged
per run capped (merge.gardenFamiliesPerRun, default 1), dry-run unless apply=True, every deletion
backed up under state/backups/.
"""
from __future__ import annotations
import datetime
import json
import shutil

from engine import merge as M
from engine import notes as N


def _body_size(note_path):
    try:
        _, body = M.parse_note(note_path.read_text(encoding="utf-8", errors="ignore"))
        return len(N._strip_recent(body).strip())
    except OSError:
        return 0


def find_stubs(cfg, max_bytes):
    stubs = []
    for note in sorted(cfg.knowledge_dir.glob("*.md")):
        if note.name in ("index.md", "README.md"):
            continue
        sz = _body_size(note)
        if 0 <= sz < max_bytes:
            stubs.append((note.stem, sz))
    return stubs


def find_families(cfg):
    """Slug families sharing a 2-segment prefix (vana-storage-*)."""
    slugs = sorted(M.valid_slugs(cfg))
    fams = {}
    for s in slugs:
        parts = s.split("-")
        if len(parts) >= 2:
            fams.setdefault("-".join(parts[:2]), []).append(s)
    return {k: v for k, v in fams.items() if len(v) > 1}


# ── family judge: 'one fragmented topic' vs 'different topics sharing a prefix' ─────────────────
def _state_path(cfg):
    space = getattr(cfg, "space", None)
    name = "garden.json" if space in (None, "default") else f"garden-{space}.json"
    return cfg.state_dir / "derived" / name


def _judged(cfg):
    return M._read_json(_state_path(cfg), {})


def _record_verdict(cfg, fam, members, verdict):
    st = _judged(cfg)
    st[fam] = {"members": sorted(members), "verdict": verdict,
               "at": datetime.date.today().isoformat()}
    M._write_json(_state_path(cfg), st)


def unjudged_families(cfg):
    """Families with no recorded verdict — or whose membership CHANGED since the last verdict
    (a new member reopens the question)."""
    st = _judged(cfg)
    out = {}
    for fam, members in find_families(cfg).items():
        rec = st.get(fam)
        if not rec or sorted(members) != rec.get("members"):
            out[fam] = members
    return out


def _judge_family(cfg, backend, members):
    """Ask the family rubric: same (-> canonical slug) or different (-> None). Doubt = different."""
    from adapters.model.base import Task
    from core.config import SENTINEL
    parts = []
    for s in members:
        p = cfg.knowledge_dir / f"{s}.md"
        fields, body = M.parse_note(p.read_text(encoding="utf-8", errors="ignore"))
        ty = M.fget(fields, "type")
        parts.append(f"### {s}.md (type: {ty[1] if ty else '?'})\n"
                     f"{N._strip_recent(body).strip()[:1200]}")
    prompt = f"{SENTINEL}\nFAMILY:\n\n" + "\n\n".join(parts)
    for _ in range(2):
        try:
            r = backend.run(Task(phase="route", system=M._rubric(cfg, "garden-family.md"),
                                 prompt=prompt, max_tokens=4000, expect_json=True))
        except Exception:
            continue
        obj = M._parse_json_lenient(r.text)
        if not obj:
            continue
        if obj.get("verdict") == "same":
            raw = str(obj.get("canonical") or "").strip().strip("[]").strip()
            if raw.endswith(".md"):  # models often echo the filename — slugify would keep '-md'
                raw = raw[:-3]
            canon = M.slugify(raw)
            if canon in members:
                return canon
        return None  # parsed 'different' (or bad canonical — treat as doubt)
    return None


def _pick_target(cfg, backend, slug, body):
    """Gardening needs the OPPOSITE bias from routing: route.md is precision-tuned ('when in
    doubt, new'), which keeps every stub forever. A dedicated merge-biased rubric picks the one
    related note to absorb the stub. The stub's own index row is removed first — with it present
    the model dutifully routes the stub into itself."""
    from adapters.model.base import Task
    from core.config import SENTINEL
    index_p = cfg.knowledge_dir / "index.md"
    index = index_p.read_text(encoding="utf-8") if index_p.exists() else ""
    index = "\n".join(ln for ln in index.splitlines() if f"[[{slug}]]" not in ln)
    prompt = (f"{SENTINEL}\nSTUB ({slug}.md):\n{body[:800]}\n\nINDEX:\n{index}")
    for _ in range(2):
        try:
            r = backend.run(Task(phase="route", system=M._rubric(cfg, "garden-target.md"),
                                 prompt=prompt, max_tokens=4000, expect_json=True))
        except Exception:
            continue
        obj = M._parse_json_lenient(r.text)
        target = M.slugify(str((obj or {}).get("target") or ""))
        if target and target not in ("none", slug) and target in M.valid_slugs(cfg):
            return target
        if obj is not None:
            return None  # a parsed, deliberate 'none'
    return None


def _absorb(cfg, victim, target, log=print):
    """Move the victim's truth (ledger + prose-as-entry) into the target's ledger and delete the
    victim (with backup + link healing). No render here — the caller compacts each touched target
    ONCE at the end (many stubs often share a parent), and even if that render is deferred the
    absorbed content is already durable in the target's ledger and visible in its Recent section."""
    vnote = cfg.knowledge_dir / f"{victim}.md"
    fields, body = M.parse_note(vnote.read_text(encoding="utf-8"))
    ty = M.fget(fields, "type")
    src = M.fget(fields, "sources")
    sources = (src[1] if src and src[0] == "list" else []) or []
    entries = list(N.load_ledger(cfg, victim))
    prose = N._strip_recent(body).strip()
    if prose:  # the stub's own words survive as one synthetic ledger entry
        entries.append({"claim": f"(merged from {victim}.md) {prose}",
                        "type": (ty[1] if ty else "reference"),
                        "entities": victim.split("-"),
                        "source": sources[0] if sources else None,
                        "merged_from": victim})
    N.append_atoms(cfg, target, entries, log=log)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = cfg.state_dir / "backups" / ts
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(vnote, backup / vnote.name)
    vnote.unlink()
    for p in (N.ledger_path(cfg, victim), N.seed_path(cfg, victim), N.meta_path(cfg, victim)):
        if p.exists():
            shutil.move(str(p), backup / p.name)
    for note in cfg.knowledge_dir.glob("*.md"):  # heal links
        txt = note.read_text(encoding="utf-8", errors="ignore")
        if f"[[{victim}]]" in txt:
            note.write_text(txt.replace(f"[[{victim}]]", f"[[{target}]]"), encoding="utf-8")
    log(f"  merged [[{victim}]] -> [[{target}]]")
    return True


def garden(cfg, place_backend, synth_backend, apply=False, log=print, max_merges=None,
           max_families=None):
    """One gardening pass over one space's KB. Dry-run by default."""
    from engine.inject import write_inject_files
    max_bytes = int(cfg.merge_cfg.get("gardenStubBytes", 600))
    if max_merges is None:
        max_merges = int(cfg.merge_cfg.get("gardenMaxMerges", 8))
    if max_families is None:
        max_families = int(cfg.merge_cfg.get("gardenFamiliesPerRun", 1))
    stubs = find_stubs(cfg, max_bytes)
    pending_fams = unjudged_families(cfg)
    log(f"garden: {len(stubs)} stub(s) <{max_bytes}B, {len(pending_fams)} unjudged famil(ies)")

    merged = []
    for slug, sz in stubs[:max_merges]:
        note = cfg.knowledge_dir / f"{slug}.md"
        _, body = M.parse_note(note.read_text(encoding="utf-8"))
        target = _pick_target(cfg, place_backend, slug, N._strip_recent(body).strip())
        if not target:
            log(f"  stub {slug} ({sz}B): no confident target — kept")
            continue
        if not apply:
            log(f"  stub {slug} ({sz}B): would merge -> [[{target}]]  (dry-run; pass --apply)")
            continue
        if _absorb(cfg, slug, target, log=log):
            merged.append((slug, target))

    # family judging: bounded per run, apply-only (it spends model calls), doubt keeps the family
    fam_merged = []
    if apply:
        for fam, members in sorted(pending_fams.items())[:max_families]:
            canon = _judge_family(cfg, place_backend, members)
            if not canon:
                _record_verdict(cfg, fam, members, "keep")
                log(f"  family {fam}: judged DIFFERENT — kept ({', '.join(members)})")
                continue
            for victim in [m for m in members if m != canon]:
                if _absorb(cfg, victim, canon, log=log):
                    fam_merged.append((victim, canon))
            _record_verdict(cfg, fam, [canon], "merged")
            log(f"  family {fam}: judged SAME — absorbed into [[{canon}]]")
    else:
        for fam, members in sorted(pending_fams.items()):
            log(f"  family {fam}: {', '.join(members)}  (unjudged; --apply lets the judge decide)")

    merged += fam_merged
    if merged:
        # one render per touched parent, however many notes it absorbed
        N.compact(cfg, synth_backend, slugs=sorted({t for _, t in merged}), force=True, log=log)
        M.rebuild_index(cfg.knowledge_dir)
        write_inject_files(cfg)
    return {"stubs": len(stubs), "families": pending_fams, "merged": merged}
