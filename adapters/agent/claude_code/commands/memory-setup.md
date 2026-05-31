---
description: Conversational onboarding — explain memory-agent, discover sources with consent, configure, backfill
---

You are driving memory-agent's onboarding. ROOT resolution: if `/ABS/PATH/TO/memory-agent` below
looks like an unreplaced placeholder you are running from the PLUGIN install — use
`${CLAUDE_PLUGIN_ROOT}` as ROOT and prefix every python3 call with
`MEMORY_AGENT_DATA="${CLAUDE_PLUGIN_DATA}"`; the DATA dir is `${CLAUDE_PLUGIN_DATA}`. Otherwise
ROOT = `/ABS/PATH/TO/memory-agent` and DATA = ROOT.

**If the KB already has notes** (DATA/knowledge/index.md exists and lists notes): this is
RECONFIGURE mode. Offer exactly: add a memory source / change settings (extraction backend,
auto-promote, background collector) / show status / run eval. Do what they pick, then stop.

**Otherwise, first-run onboarding — keep the whole dialog tight (≤3 questions), never narrate
commands, and make "use defaults" a complete answer at every step:**

1. **Explain first, in ≤5 of your own sentences:** it compiles their AI conversation history into
   a knowledge base of markdown notes THEY own (at DATA/knowledge/ — every fact carries which
   sessions it came from and a log of superseded beliefs); raw files are never modified; with a
   local model running, transcripts never leave the machine — consolidation uses their Claude plan
   unless they choose otherwise. Ask if they want to proceed.

2. **Three questions, defaults stated** (run `python3 ROOT/mem.py status` first to detect the
   extraction backend):
   - Extraction: report what auto-detection found ("local model server ✓" or "no local server —
     I'll use your Claude plan (haiku); install LM Studio anytime to make extraction free and
     fully private"). Confirm or let them override.
   - Updates: auto-apply with timestamped backups (default) or stage every change for review
     (`merge.autoPromote`)?
   - macOS only: install the silent hourly background collector (launchd)? Default yes.

3. **Source discovery — consent before scanning.** Say what you would look at: their Claude Code
   history (hooks already cover the future; the backfill covers the past), `~/.opencode`, and the
   top level of `~/Downloads`/`~/Desktop` for AI export files (`conversations.json`, `*chatgpt*`,
   `*claude*` archives) — plus any folder they name. Scan ONLY what they approve. Present findings
   as a checklist with sizes/counts; they pick what to include. Never roam beyond approved paths.

4. **Execute (quietly):**
   - Write their choices as a config override to DATA/config.json (top-level keys only, e.g.
     `{"merge": {"autoPromote": false}}`) — it overlays the shipped config and survives updates.
   - If launchd approved: run `python3 ROOT/install.py` (with MEMORY_AGENT_DATA exported for
     plugin installs).
   - `python3 ROOT/mem.py capture` (first run auto-baselines — nothing floods).
   - For each ONE-TIME source (exports, old-machine dumps): leave the files where they are
     (raw is immutable, never copy gigabytes) and run the backfill Workflow in the background:
     `{scriptPath: "ROOT/engine/backfill.js", args: {rawDir: "<source dir>", derivedDir:
     "DATA/state/derived", maxChunks: 400, model: "sonnet"}}` — format hints live in
     ROOT/input/handlers/. For ONGOING sources (another tool that keeps producing), instead add a
     generic adapter entry to the config override (`agents` list: name, transcripts dir,
     inject file).
   - When the backfill completes: `python3 ROOT/mem.py adopt`, then run merge cycles
     (`/memory-refresh` logic) until `merge --stage check` reports `newAtoms` < 8 — routing is
     batched, so large backlogs take several cycles.

5. **The payoff — always end here:** read DATA/knowledge/index.md and tell them "here's what I now
   know about you" with ~5 specific facts from their actual notes. Then, in two sentences: where
   the files live, that editing a note directly is the way to correct anything, and that
   `/memory-refresh` (or a `/loop 4h /memory-refresh`) keeps it current.
