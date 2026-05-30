---
description: Silently consolidate accumulated memory atoms into the KB (auto-promote); one-line status
---

Drain the memory pipeline for the repo at `/ABS/PATH/TO/memory-agent` (ROOT). A launchd agent
already does capture + local extraction in the background; this command's job is the strong-model
MERGE, which needs a live session. **Be as invisible as possible: no narration, no headers, no
multi-line reports. Respond with exactly ONE short line unless notes were promoted or something
failed.**

1. Run: `python3 ROOT/mem.py capture && python3 ROOT/mem.py refresh --min-growth 75000 && python3 ROOT/mem.py merge --stage check`
   (idempotent backstop for launchd; run via Bash in the background if extraction kicks in, and wait).
2. If `newAtoms` < 8: reply with one line — `memory: idle (N atoms pending)` — and stop.
3. Otherwise invoke the **Workflow** tool with `{scriptPath: "ROOT/engine/merge.js", args: {root: "ROOT"}}`.
   Finalize auto-promotes per config (`merge.autoPromote: true`); backups land in `state/backups/`.
4. After promotion, reply with one line — `memory: N atoms → updated X notes, created Y` — and send a
   PushNotification with the same line ONLY if notes were created or a conflict was recorded. On any
   error, reply with one line describing it.

Suitable for a sparse loop (e.g. `/loop 4h /memory-refresh`). Audit trail lives in
`ROOT/state/logs/refresh.log`, `state/backups/`, and `state/derived/merge/history/` — look whenever
you want; the loop won't push it at you.
