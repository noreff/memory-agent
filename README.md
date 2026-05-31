# memory-agent

A **universal agentic memory** for coding agents. Point it at any tool's conversation history; it
builds and keeps fresh one shared markdown knowledge base, and feeds that memory back into whatever
agent you use — so a fresh session is instantly *in the loop* on your machine, projects, and
preferences.

## Why

The frontier converged on this architecture in 2026: ChatGPT's memory moved to background
consolidation from raw chat history; Anthropic's first-party memory tool is plain files read
index-first with no vector DB. memory-agent is that same architecture — **except you own it**:

- **Your memory is markdown files on your disk.** Read them, edit them, grep them, version them.
  No hosted store, no embeddings infra, no database to trust.
- **Every fact carries receipts.** Notes record `sources` (which sessions a fact came from) and
  `conflicts` (what used to be true, superseded when, by what). Contradictions are resolved by
  recency and *logged*, never silently overwritten.
- **No stub spam.** New topics earn a note only after enough independent facts accumulate; one-off
  factoids wait in a pending pool instead of polluting the KB.
- **It can't eat itself.** Injected memory is stripped before extraction, so the system never
  re-memorizes its own output.
- **Invisible by default, auditable on demand.** A background agent collects and extracts; merges
  auto-apply with timestamped backups; logs, backups, and run archives are there when you want
  them and never pushed at you.
- **Any model, any agent.** Extraction runs on a free local model (LM Studio); consolidation on a
  strong model (Claude subscription subagents or any API). Swap per-phase in config. Host adapters
  make the same KB serve multiple agents.

- **Source-agnostic** — any folder of files (Claude Code / OpenCode `.jsonl`, plain markdown,
  arbitrary docs). The ingest step self-discovers each format.
- **Agent-agnostic** — a thin adapter per host tool (Claude Code today; OpenCode / others via the
  `generic` adapter). One config selects which tools feed and read the KB.
- **Model-agnostic** — a pluggable backend per phase: local LLMs (LM Studio), a cloud API, or the
  Claude Code subscription. Never hardcoded.
- **Backfill-first** — bootstrap the whole KB from your existing history in one pass, then keep it
  current incrementally.

Output is plain markdown notes. No vector DB — at personal scale an LLM reading a structured
`index.md` beats similarity search. See [`DESIGN.md`](DESIGN.md) for the full architecture.

## How it works (three flows, three seams)

```
capture  notice new transcripts (manifest-diff)            → state/inbox/        (compute-free)
refresh  inbox → distill+chunk → extract atoms             → state/derived/atoms (cheap model, local-friendly)
merge    route atoms into existing notes → re-synth touched → knowledge/         (strong model)
inject   knowledge/ → into a starting session                                    (read-only)
```

Two-speed processing by design: **refresh** is the cheap, frequent atom collector (safe to run on a
local model); **merge** is the consolidation step that needs judgment (routes each atom into the
existing note it belongs to, creates a new note only when a topic accumulates ≥3 atoms, records
conflicts with recency). Note frontmatter is always assembled in code, never by the model. A
periodic full recompile (the backfill Workflow) remains the deep-clean.

| Seam | What it abstracts | Where |
|---|---|---|
| input-handler | how to read a format | `input/` |
| agent-adapter | where/when to capture, how to inject, whether the host offers compute | `adapters/agent/` |
| model-adapter | what runs the LLM work (local / cloud / subscription) | `adapters/model/` |

## Quick start (Claude Code)

**One command:**

```bash
curl -fsSL https://raw.githubusercontent.com/noreff/memory-agent/main/install.sh | sh
```

(Installs as a Claude Code plugin — no source to manage; falls back to a git install if the
`claude` CLI is absent.) Prefer doing it by hand? `claude plugin marketplace add
noreff/memory-agent && claude plugin install memory-agent@memory-agent`, or the same two
`/plugin …` commands inside any session.

Then say **`/memory-setup`** — the agent walks you through everything conversationally: shows you
what it would remember from your last session, asks three questions, finds your history (with your
consent), backfills in the background, and ends with "here's what I now know about you." Hooks and
commands are live from install; memory data lives in the plugin's persistent data dir (survives
updates); even the optional macOS background collector is installed by the agent during setup if
you say yes.

**Or clone:**

```bash
git clone https://github.com/noreff/memory-agent && cd memory-agent
python3 install.py   # hooks, /memory-refresh command, macOS launchd extractor, manifest baseline
```

Requirements: Python 3.10+ (stdlib only — zero dependencies) and Claude Code itself. That's it:
extraction defaults to `auto` and resolves to whatever you have — a local OpenAI-compatible server
([LM Studio](https://lmstudio.ai) on `:1234`) if one is running, otherwise **your Claude plan**
(the `claude` CLI, haiku for extraction), otherwise an `ANTHROPIC_API_KEY`. **A local model is a
bonus, not a dependency**: when LM Studio is up, extraction becomes free and your transcripts never
leave the machine; the system detects it automatically, per run. One policy line: the *background*
extractor only ever uses the local backend (a daemon shouldn't spend your Claude plan headlessly) —
without one, it captures only, and atoms extract on your next in-session `/memory-refresh`.
Consolidation uses the Claude Code **Workflow** tool (ships with current Claude Code) or any
backend via `mem.py merge --backend …`. The launchd extractor is macOS-only; on Linux schedule
`mem.py capture && mem.py refresh --min-growth 75000` with cron/systemd. First run is always a safe
baseline — existing history is never queued by accident; you bootstrap it deliberately with the
backfill. Plugin installs customize everything by dropping a `config.json` into the plugin data
dir (overrides the shipped defaults, survives updates).

That's it. From now on: sessions are captured by hooks, a launchd agent extracts atoms in the
background (local model, fully silent — log at `state/logs/refresh.log`), and consolidation drains
whenever a session runs `/memory-refresh` (give it a sparse loop, e.g. `/loop 4h /memory-refresh`;
replies are one line). Merges auto-apply with backups (`merge.autoPromote`; set `false` if you want
a review gate — assembled notes then stage in `state/derived/merge/out/` until you promote).

### Manual setup (what `install.py` does — for re-installing by hand)

1. **Install hooks.** Merge [`adapters/agent/claude_code/settings.snippet.json`](adapters/agent/claude_code/settings.snippet.json)
   into `~/.claude/settings.json` under `"hooks"` (replace the path with your clone's absolute path).
   SessionStart injects memory; SessionEnd/PreCompact enqueue the finished session (compute-free).
2. **Baseline once:** `python3 mem.py capture --baseline` — records existing transcripts as *seen* so
   only sessions created *after* now get captured (your backfill covers the rest).
3. **(Optional) backfill** the KB from existing history first — see "Backfill" below.

Uninstall: `python3 install.py --uninstall`.

## CLI

```
python3 mem.py status                  paths, enabled adapters, inbox depth
python3 mem.py capture [--baseline]    scan adapters; enqueue new sessions (baseline = record only)
python3 mem.py inject [--cwd DIR]      print the SessionStart payload (debug)
python3 mem.py refresh [opts]          drain inbox → extract atoms into the atom store (atoms ONLY)
    --backend NAME   override the extract backend: local | cloud | subscription | stub
    --limit N        process at most N pending sessions
    --dry-run        distill only; no model calls
python3 mem.py merge [opts]            consolidate unrouted atoms into the KB (strong model)
    --stage S        all | prepare | finalize | promote  (prepare/finalize = the mechanical halves
                     driven by the merge Workflow; default all = completion-backend pipeline)
    --backend NAME   override the route+synth backend for --stage all
    --dry-run        route only: print the per-atom routing table, synthesize nothing
    --promote        apply assembled notes to knowledge/ (else they stage for review)
```

Both are **safe by default**: `refresh` only writes the atom store; `merge` assembles notes under
`state/derived/merge/out/` for review and touches `knowledge/` only on promote (with a backup under
`state/backups/<ts>/`, an index rebuild, and only then marking atoms consumed — an abandoned staging
run leaves everything unrouted, so re-running is always safe). Set `merge.autoPromote: true` once you
trust it. The per-atom verdicts are: `into:<note>` (preferred — update/supersede an existing note),
`new:<topic>` (gated: a note is created only at ≥ `merge.newNoteThreshold` atoms; below that the
atoms wait in a pending pool), `duplicate`, `discard`.

## Configuration (`config.json`)

```jsonc
{
  "agents": [
    { "adapter": "claude-code", "enabled": true },
    { "adapter": "generic", "name": "my-tool", "enabled": false,
      "transcripts": { "dir": "~/.my-tool/logs", "format": "auto" },
      "inject": { "file": "~/.my-tool/memory.md" } }
  ],
  "model": {
    "extract": { "backend": "local", "model": "qwen/qwen3.6-35b-a3b" },
    "route":   { "backend": "subscription", "model": "sonnet" },
    "merge":   { "backend": "subscription", "model": "sonnet" }
  }
}
```

- **`agents` is a list** — enable several tools; they share one KB (provenance tags the source).
- **Add any tool with zero code** via a `generic` entry (transcripts dir + inject file).
- **`model.<phase>.backend`** swaps compute per phase. `local` (LM Studio, free/private),
  `cloud` (API key, metered), `subscription` (`claude -p`, no key, in-session only).

## Backfill (populate the KB from your existing history — the key feature)

Bootstrap the whole KB from everything you already have. The richest path is the Claude Code
Workflow `engine/backfill.js`: tool-using subagents self-discover each file format, chunk huge
sessions, extract atoms, globally de-duplicate, and synthesize canonical notes — raw files are
never modified. From a Claude Code session:

```
Workflow tool → { scriptPath: "<ROOT>/engine/backfill.js",
                  args: { rawDir: "/abs/path/to/raw", derivedDir: "/abs/path/to/state/derived",
                          maxChunks: 400, model: "sonnet" } }
```

Then adopt the result into your live KB:

```bash
python3 mem.py adopt        # state/derived/notes/ → knowledge/ + index build
```

(Reference run: 103 sessions → 6,419 atoms → 33 canonical notes in ~47 minutes.) For a fully
local/offline backfill, seed the inbox with your files and run `mem.py refresh` + `mem.py merge
--backend local`.

## Eval (quality as a number, not a feeling)

`mem.py eval` scores three things against your own gold set (`eval/` is git-ignored — personal):
**recall** (re-extract frozen fixtures, do known facts reappear), **lookup** (pick the right note
from the index, answer from its body — the intended flow), **inject** (what's answerable from the
index alone). Every run appends to `eval/history.jsonl`, so prompt/model changes show up as score
deltas, not vibes.

## Security model

Memory built from conversations is an injection surface: transcript content could try to steer the
models that process it. Structural defenses: the KB is only ever written by code, from staged
artifacts, after the gate (`promote` backs up, then copies from `out/` — agents never write
`knowledge/` directly); note frontmatter is built in code; atom payloads are fenced as untrusted
data in every prompt; the system's own internal model calls are sentinel-tagged so they can't be
re-memorized; raw transcripts are never modified. Residual risk: consolidation agents run with the
host's tool permissions — review your platform's agent sandboxing if you process untrusted
transcripts. Promoted notes are injected into future sessions, so treat `knowledge/` with the same
care as CLAUDE.md.

## Backends & privacy

The subscription is a flat-rate compute pool but only redeemable from inside a live session, so heavy
processing piggybacks on sessions you're already in. Local models exist for privacy and to spare your
rate-limit budget. Transcripts never leave your machine unless you choose a cloud/subscription backend
for processing. `state/`, `knowledge/`, and raw transcripts are git-ignored.

## Repo layout

```
core/        config + on-disk protocol (pipeline.md) + prompts/ (route/merge/new-note rubrics)
tests/       python3 -m unittest discover tests (stdlib, no deps)
input/       format handlers + mechanical chunker (echo-suppressed: injected memory is never re-mined)
adapters/
  agent/     base + claude_code/ (hooks, /memory-refresh command) + generic + contract (adapter.md)
  model/     base + local/cloud/subscription/stub + contract (adapter.md)
engine/      capture · inject · refresh (atoms) · merge.py + merge.js (consolidation) · backfill
mem.py       CLI            install.py  hook + command installer
```
