"""MAP step: one distilled conversation -> atomic knowledge notes, via a local model.

Uses PLAIN JSON, NOT LM Studio's `response_format: json_schema`. The MLX backend loops under
constrained decoding (LM Studio's Outlines doesn't forward a repetition penalty to MLX-LM —
see Outlines #1131), producing degenerate repeats. So we ask for JSON, then parse leniently
and validate / normalize / dedup in CODE, with a loop guard (retry once on parse failure or
finish=='length'). Provenance (source/date) is stamped in code. Resumable (manifest-gated)."""
from __future__ import annotations
import json
import re
import time
import urllib.request
from pathlib import Path

from config import (DISTILLED_DIR, LMSTUDIO, MAP_MAX_TOKENS, MAP_MODEL, MAP_TEMP,
                    MAP_TOP_P, NOTES_DIR)
from store import convo_date, load_manifest, save_manifest, sha256, source_id

TYPES = {"user", "feedback", "project", "reference"}
CONFS = {"high", "medium", "low"}

SYSTEM = """You are the EXTRACT step of a personal knowledge-base pipeline. You read ONE AI-coding-assistant conversation transcript and emit atomic "knowledge notes" whose SOLE purpose is to make a FUTURE AI agent instantly "in the loop" on this user's machine, projects, and preferences.

The transcript is a rendered Claude Code session. `→` lines are tool calls; `> [tool result]` blocks are (capped) tool OUTPUT — mine them for durable facts (installed services, ports, paths, running projects), don't just discard them.

## What to extract (PRECISION OVER RECALL — when in doubt, leave it out)
Only durable knowledge that would be EXPENSIVE for a future agent to rediscover. One atomic fact per note. Classify each into exactly one type:
- user      — who the user is, their environment, hardware, setup, language.
- feedback  — how the user wants the agent to WORK (corrections, approach preferences, confirmation habits).
- project   — state/intent/decisions about a specific project or the machine, not derivable from code/git.
- reference — a reusable technical fact, gotcha, or fix worth remembering across projects.

## CRITICAL RULES
1. CURRENT-STATE, NOT GONE: capture the lesson or CURRENT state. If something was removed/uninstalled/abandoned during THIS session, the note must say so (type=project), not describe its internals as if it still exists.
2. NORMALIZE TO ENGLISH even if the conversation is in another language.
3. DROP transient task chatter, step-by-step narration, raw command output, and anything obvious from the repo/git.
4. Never repeat the same note.

## Output format
Return ONLY a JSON object, with NO prose and NO markdown fences:
{"notes": [{"claim": "...", "type": "...", "topic": "...", "confidence": "..."}, ...]}
Each note has EXACTLY these four fields:
- "claim": one crisp, self-contained sentence (the atomic fact), in English.
- "type": exactly one of "user", "feedback", "project", "reference".
- "topic": a short topic slug (2-4 words).
- "confidence": exactly one of "high", "medium", "low".
If nothing is worth keeping, return {"notes": []}."""


def _call(messages, max_tokens, anti_repeat=False):
    payload = {"model": MAP_MODEL, "messages": messages, "temperature": MAP_TEMP,
               "top_p": MAP_TOP_P, "max_tokens": max_tokens}
    if anti_repeat:  # used on the retry to break repetition loops (plain-JSON forwards these)
        payload["top_p"] = 0.3
        payload["presence_penalty"] = 1.3
        payload["frequency_penalty"] = 0.7
    req = urllib.request.Request(f"{LMSTUDIO}/chat/completions",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=900).read())


def _extract_json(resp):
    """Return (notes_list | None, finish_reason). None means unparseable."""
    choice = resp["choices"][0]
    finish = choice.get("finish_reason")
    msg = choice["message"]
    raw = (msg.get("content") or "").strip() or (msg.get("reasoning_content") or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if m:
        raw = m.group(1).strip()
    m = re.search(r"(\{.*\}|\[.*\])", raw, re.DOTALL)
    if not m:
        return None, finish
    try:
        obj = json.loads(m.group(1))
    except Exception:
        return None, finish
    notes = obj.get("notes") if isinstance(obj, dict) else obj
    return (notes if isinstance(notes, list) else None), finish


def _normalize(n):
    if not isinstance(n, dict):
        return None
    claim = str(n.get("claim") or n.get("fact") or n.get("statement") or "").strip()
    if not claim:
        return None
    t = str(n.get("type", "")).strip().lower()
    c = str(n.get("confidence", "")).strip().lower()
    return {
        "claim": claim,
        "type": t if t in TYPES else "project",
        "topic": str(n.get("topic", "")).strip() or "general",
        "confidence": c if c in CONFS else "medium",
    }


def _clean(notes):
    """Normalize, validate, and dedup (dedup also neutralizes any loop output)."""
    out, seen = [], set()
    for n in notes or []:
        nn = _normalize(n)
        if not nn:
            continue
        key = nn["claim"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(nn)
    return out


def extract_notes(text):
    """Returns (clean_notes, finish_reason, retried, seconds)."""
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"TRANSCRIPT:\n\n{text}"}]
    parsed, finish, retried, dt = None, None, False, 0.0
    for attempt in range(2):  # one retry on parse failure / length loop
        t0 = time.time()
        resp = _call(msgs, MAP_MAX_TOKENS, anti_repeat=(attempt == 1))
        dt = time.time() - t0
        parsed, finish = _extract_json(resp)
        if parsed is not None and finish != "length":
            break
        retried = attempt == 0
    return _clean(parsed), finish, retried, dt


def extract_file(path: Path, manifest: dict) -> int:
    text = path.read_text(encoding="utf-8")
    h = sha256(text)
    prev = manifest["processed"].get(path.name)
    if prev and prev.get("hash") == h:
        print(f"  skip (unchanged): {path.name}")
        return 0
    src, date = source_id(path.name), convo_date(text)
    notes, finish, retried, dt = extract_notes(text)
    for n in notes:                       # stamp provenance in code
        n["source"], n["date"] = src, date
    NOTES_DIR.mkdir(exist_ok=True)
    (NOTES_DIR / f"{path.stem}.json").write_text(
        json.dumps(notes, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest["processed"][path.name] = {"hash": h, "source": src, "date": date,
                                        "n_notes": len(notes), "finish": finish,
                                        "retried": retried}
    save_manifest(manifest)
    flag = (" ⚠retry" if retried else "") + (" ⚠LEN" if finish == "length" else "")
    print(f"  {path.name}: {len(notes)} notes ({dt:.0f}s){flag}")
    return len(notes)


def main():
    manifest = load_manifest()
    total, lengthy, retries = 0, 0, 0
    for p in sorted(DISTILLED_DIR.glob("*.md")):
        try:
            total += extract_file(p, manifest)
            info = manifest["processed"].get(p.name, {})
            lengthy += info.get("finish") == "length"
            retries += bool(info.get("retried"))
        except Exception as e:
            print(f"  ERROR {p.name}: {e}")
    print(f"\nMAP done. {total} notes across {len(manifest['processed'])} convos. "
          f"retries={retries} hit_length={lengthy}")


if __name__ == "__main__":
    main()
