"""engine/evals — the feedback signal. Two regression checks, run after any prompt/model/threshold
change, so quality is a number instead of a vibe:

  inject — a fresh model gets ONLY the SessionStart inject payload and answers gold questions
           (eval/gold.jsonl). Scores end-to-end memory usefulness: extraction -> merge -> index ->
           inject. Batched into one completion call.
  recall — re-extracts frozen fixture transcripts (eval/fixtures/<name>.txt) with the CURRENT
           extract config and checks that known key facts reappear (<name>.expect.json). Catches
           extraction regressions (prompt drift, model swaps, token-limit truncation).

The eval DATA (gold.jsonl, fixtures/) contains personal facts and stays git-ignored, like the KB;
only this code is tracked. Scoring is case-insensitive substring matching — deterministic, no
LLM judge."""
from __future__ import annotations
import json
import re


def _load_gold(cfg):
    p = cfg.root / "eval" / "gold.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _hit(text, keywords):
    t = text.lower()
    return any(k.lower() in t for k in keywords)


def eval_inject(cfg, backend, cwd=None, log=print):
    """Answer all gold questions from the inject payload alone, in ONE batched call."""
    from adapters.model.base import Task
    from engine.inject import build_payload
    gold = _load_gold(cfg)
    if not gold:
        log("no eval/gold.jsonl — nothing to score")
        return None
    payload = build_payload(cfg, cwd=cwd)
    questions = "\n".join(f"{i + 1}. {g['q']}" for i, g in enumerate(gold))
    system = ("You are an agent whose ONLY knowledge is the MEMORY document below. Answer each "
              "numbered question in ONE short line, strictly from the memory (Read-style note "
              "lookups are not available — use only what is written). If the memory does not "
              "contain the answer, write UNKNOWN. OUTPUT FORMAT: exactly one line per question, "
              "'N. answer'. NO preamble, NO analysis, NO thinking out loud, NO headers — your very "
              "first output line must start with '1.'.")

    def _ask(extra=None):
        r = backend.run(Task(phase="eval", system=system,
                             prompt=f"MEMORY:\n{payload}\n\nQUESTIONS:\n{questions}",
                             max_tokens=6000, extra=extra))
        answers = {}
        for ln in r.text.splitlines():
            m = re.match(r"^\s*(\d+)[.):\-]\s*(.+)$", ln)
            if m and m.group(2).strip().strip("*"):
                answers[int(m.group(1))] = m.group(2).strip()
        return answers

    answers = _ask()
    if len(answers) < len(gold) / 2:  # reasoning preamble ate the budget — retry constrained
        log(f"  (only {len(answers)} parseable answers — retrying with anti-repeat constraints)")
        answers = _ask(extra={"top_p": 0.3, "presence_penalty": 1.0})
    hits = []
    for i, g in enumerate(gold):
        ans = answers.get(i + 1, "")
        ok = _hit(ans, g["expect_any"])
        hits.append(ok)
        log(f"  {'PASS' if ok else 'MISS':4} {g['id']:22} -> {ans[:90]}")
    score = sum(hits) / len(hits)
    log(f"inject score: {sum(hits)}/{len(hits)} = {score:.2f}  (backend={backend.name})")
    return {"mode": "inject", "score": score, "n": len(hits), "backend": backend.name}


def eval_lookup(cfg, backend, log=print):
    """The headline metric: simulate the intended agentic flow. Stage 1 — from the index alone,
    pick which note should contain each answer. Stage 2 — answer each question from that note's
    full body. inject-mode is the index-only lower bound; this measures index + one Read."""
    from adapters.model.base import Task
    gold = _load_gold(cfg)
    if not gold:
        log("no eval/gold.jsonl — nothing to score")
        return None
    index_p = cfg.knowledge_dir / "index.md"
    index = index_p.read_text(encoding="utf-8") if index_p.exists() else ""
    slugs = {p.stem for p in cfg.knowledge_dir.glob("*.md")} - {"index", "README"}

    # stage 1: route each question to a note slug
    questions = "\n".join(f"{i + 1}. {g['q']}" for i, g in enumerate(gold))
    r = backend.run(Task(
        phase="eval",
        system=("Below is the INDEX of a personal knowledge base. For each numbered question, "
                "output 'N. <note-slug>' — the [[slug]] of the single note most likely to contain "
                "the answer. NO preamble, NO analysis; first line starts with '1.'."),
        prompt=f"INDEX:\n{index}\n\nQUESTIONS:\n{questions}", max_tokens=3000,
    ))
    picks = {}
    for ln in r.text.splitlines():
        m = re.match(r"^\s*(\d+)[.):\-]\s*\[?\[?([a-z0-9-]+)", ln.strip())
        if m and m.group(2) in slugs:
            picks[int(m.group(1)) - 1] = m.group(2)

    # stage 2: answer from the picked note's body, grouped one call per note
    by_note = {}
    for i, g in enumerate(gold):
        by_note.setdefault(picks.get(i), []).append((i, g))
    answers = {}
    for slug, items in by_note.items():
        if slug is None:
            continue
        body = (cfg.knowledge_dir / f"{slug}.md").read_text(encoding="utf-8")
        qs = "\n".join(f"{n + 1}. {g['q']}" for n, (_, g) in enumerate(items))
        r = backend.run(Task(
            phase="eval",
            system=("Answer each numbered question in ONE short line strictly from the NOTE below. "
                    "Write UNKNOWN if the note does not contain the answer. NO preamble; first "
                    "line starts with '1.'."),
            prompt=f"NOTE:\n{body}\n\nQUESTIONS:\n{qs}", max_tokens=2000,
        ))
        local = {}
        for ln in r.text.splitlines():
            m = re.match(r"^\s*(\d+)[.):\-]\s*(.+)$", ln)
            if m:
                local[int(m.group(1)) - 1] = m.group(2).strip()
        for n, (gi, _) in enumerate(items):
            answers[gi] = local.get(n, "")

    hits = []
    for i, g in enumerate(gold):
        ans = answers.get(i, "")
        ok = _hit(ans, g["expect_any"])
        hits.append(ok)
        log(f"  {'PASS' if ok else 'MISS':4} {g['id']:22} [{picks.get(i, '-'):38}] -> {ans[:70]}")
    score = sum(hits) / len(hits)
    log(f"lookup score: {sum(hits)}/{len(hits)} = {score:.2f}  (backend={backend.name})")
    return {"mode": "lookup", "score": score, "n": len(hits), "backend": backend.name}


def eval_recall(cfg, backend, log=print):
    """Re-extract frozen fixtures with the current config; check key facts reappear."""
    from engine.refresh import extract_atoms
    fdir = cfg.root / "eval" / "fixtures"
    fixtures = sorted(fdir.glob("*.txt")) if fdir.exists() else []
    if not fixtures:
        log("no eval/fixtures/*.txt — nothing to score")
        return None
    total, hit_n = 0, 0
    for fx in fixtures:
        expect_p = fx.with_suffix("").with_suffix(".expect.json")
        if not expect_p.exists():
            expect_p = fx.parent / (fx.stem + ".expect.json")
        if not expect_p.exists():
            log(f"  {fx.name}: no .expect.json — skipped")
            continue
        expects = json.loads(expect_p.read_text(encoding="utf-8"))
        atoms = extract_atoms(backend, fx.read_text(encoding="utf-8"), "eval", "eval")
        blob = " ".join(a["claim"].lower() for a in atoms)
        for e in expects:
            ok = _hit(blob, e["any"])
            total += 1
            hit_n += ok
            log(f"  {'PASS' if ok else 'MISS':4} {fx.stem}:{e['label']}")
        log(f"  {fx.stem}: {len(atoms)} atoms extracted")
    if not total:
        return None
    score = hit_n / total
    log(f"recall score: {hit_n}/{total} = {score:.2f}  (backend={backend.name})")
    return {"mode": "recall", "score": score, "n": total, "backend": backend.name}
