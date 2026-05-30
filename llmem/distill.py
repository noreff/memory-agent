"""Mechanical distillation: collapse bulky tool-result blocks, keep the signal.

A rendered transcript is mostly tool output (file dumps, `ps`, build logs). We cap
each `<details>← tool result` block to its first few lines (where mined facts like
paths/ports usually live) and drop the rest. No model involved — fast and lossless
of the parts we care about (the raw original stays immutable as ground truth)."""
from __future__ import annotations
from pathlib import Path

from config import DISTILLED_DIR, RAW_DIR, TOOL_RESULT_MAX_LINES


def distill_text(text: str) -> str:
    out, in_details, kept = [], False, 0
    for line in text.splitlines():
        if "<details>" in line:
            in_details, kept = True, 0
            out.append("> [tool result]")
            continue
        if "</details>" in line:
            in_details = False
            continue
        if in_details:
            s = line.strip()
            if not s or s.startswith("```") or s.startswith("<summary"):
                continue
            if kept < TOOL_RESULT_MAX_LINES:
                out.append("> " + line.rstrip())
                kept += 1
            elif kept == TOOL_RESULT_MAX_LINES:
                out.append("> …(capped)")
                kept += 1
            continue
        out.append(line)
    # collapse 3+ blank lines to 1
    res, blanks = [], 0
    for line in out:
        if not line.strip():
            blanks += 1
            if blanks <= 1:
                res.append("")
        else:
            blanks = 0
            res.append(line)
    return "\n".join(res)


def main():
    DISTILLED_DIR.mkdir(exist_ok=True)
    total_in = total_out = 0
    for src in sorted((RAW_DIR / "claude-code").glob("*.md")):
        raw = src.read_text(encoding="utf-8")
        dist = distill_text(raw)
        (DISTILLED_DIR / src.name).write_text(dist, encoding="utf-8")
        total_in += len(raw); total_out += len(dist)
        print(f"  {src.name}: {len(raw)//1024}KB -> {len(dist)//1024}KB")
    if total_in:
        print(f"\nDistilled {RAW_DIR}: {total_in//1024}KB -> {total_out//1024}KB "
              f"({100*total_out//total_in}% kept)")


if __name__ == "__main__":
    main()
