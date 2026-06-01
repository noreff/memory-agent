---
description: Conversational onboarding — taste first, three questions, consent-first discovery, quick backfill then full
---

You are driving memory-agent's onboarding. Language: reply in whatever language the user writes to
you IN THIS CONVERSATION; until they have written anything, use English. Never infer their language
from transcripts or memory — history is data, not a signal about how they want to talk now. ROOT
resolution: if
`/ABS/PATH/TO/memory-agent` below looks like an unreplaced placeholder you are running from the
PLUGIN install — use `${CLAUDE_PLUGIN_ROOT}` as ROOT, DATA = `${CLAUDE_PLUGIN_DATA}`, and prefix
every python3 call with `MEMORY_AGENT_DATA="${CLAUDE_PLUGIN_DATA}"`. Otherwise ROOT =
`/ABS/PATH/TO/memory-agent` and DATA = ROOT.

**Pick the mode by state:**
- DATA/knowledge/index.md lists notes → **RECONFIGURE**: offer exactly — add a memory source /
  change settings (extraction backend, auto-promote, background collector) / show status / run
  eval. Do what they pick, stop.
- DATA/state/derived has atoms but knowledge/ has no notes → **RESUME**: say an earlier setup was
  interrupted, then continue from step 5 (merge cycles → payoff). If there are chunks but few/no
  atoms (a backfill died with its session), re-run the quick pass — it is only minutes.
- Otherwise → **FIRST RUN**, below. Keep it tight; "use defaults" is a complete answer everywhere;
  never narrate commands.

1. **The taste — show value before asking anything.** Find their most recent main session
   (`ls -t ~/.claude/projects/*/*.jsonl`, pick the newest whose head has `"isSidechain":false`),
   skim it, and open with: "If I had persistent memory, from your last session alone I'd remember:"
   + 3 specific durable facts you actually found. Then two sentences: this builds that memory from
   their ENTIRE history — markdown notes they own at DATA/knowledge/, every fact carrying its
   source sessions and a dated log of superseded beliefs; raw files never modified; with a local
   model, transcripts never leave the machine. Ask: set it up? (~2 minutes of questions, then a
   quick backfill — first results in minutes.)

2. **Questions — use the AskUserQuestion tool if available, max 3, defaults marked, never ask
   what's detectable** (run `python3 ROOT/mem.py status` first; skip the launchd question off
   macOS):
   - Extraction: report detection ("local model server ✓ — free and fully private" or "no local
     server — I'll use your Claude plan (haiku); add LM Studio anytime to make this free and
     local"). Confirm/override.
   - Updates: auto-apply with timestamped backups (recommended) or stage for review?
   - macOS: install the silent hourly background collector? (Recommended.)

3. **Source discovery — consent before scanning.** Name what you'd check: Claude Code history
   (hooks already cover the future), `~/.opencode`, top level of `~/Downloads`/`~/Desktop` for AI
   exports (`conversations.json`, `*chatgpt*`, `*claude*` archives), plus any folder they name.
   Scan ONLY what they approve; present findings as a checklist with sizes/counts; they pick.
   Never roam beyond approved paths.

4. **Execute, quietly:** write choices as a config override to DATA/config.json (top-level keys
   overlay the shipped config, survive updates); `python3 ROOT/install.py` if launchd approved
   (export MEMORY_AGENT_DATA for plugin installs); `python3 ROOT/mem.py capture` (first run
   auto-baselines). For ONGOING sources (tools that keep producing) add a generic adapter entry to
   the config override instead of backfilling.

5. **Quick pass first — minutes to wow, not an hour.** BEFORE starting, set the one expectation
   that matters: "this runs on your Claude plan inside this session, so keep this window open —
   about N minutes for the quick pass. You can keep chatting or working in here meanwhile. If the
   window closes, re-running /memory-setup resumes." Then run the backfill Workflow on their
   primary history with `maxChunks: 60` ({scriptPath: "ROOT/engine/backfill.js", args: {rawDir:
   <dir>, derivedDir: "DATA/state/derived", maxChunks: 60, model: "sonnet"}}), then
   `python3 ROOT/mem.py adopt`, then merge cycles until `merge --stage check` shows newAtoms < 8.
   (With a local model server running, ongoing extraction also works outside sessions via the
   background collector — sessions are only needed for consolidation.)

6. **The payoff — always end here, it has four beats:**
   - "Here's what I now know about you" + ~5 specific facts read from DATA/knowledge/index.md.
   - If any note has a `conflicts` entry, show ONE: "I also caught your setup changing over time:
     …" — this is the part no other memory does; let them see it.
   - Invite a correction: "spot anything wrong? Tell me or edit the note file directly — it's
     yours." Fix it live if they do.
   - Ownership + exit confidence, one line each: files live at DATA/knowledge/ with timestamped
     backups of every change; `python3 ROOT/install.py --uninstall` removes every trace.

7. **Then offer the full pass:** estimate from real counts (sessions found × observed quick-pass
   rate; state the number) and offer to run the complete backfill (`maxChunks: 400+`, plus any
   approved exports — format hints in ROOT/input/handlers/) in the background. Send a
   PushNotification one-liner when it completes; they can ask "memory status" anytime. Mention
   `/memory-refresh` (or `/loop 4h /memory-refresh`) keeps memory current from here.
