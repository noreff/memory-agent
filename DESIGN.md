# memory-agent — design (agreed 2026-06-08)

A **universal agentic memory repository**: point it at any tool's history, it builds and keeps
fresh one shared markdown knowledge base, and feeds that memory back into whatever agent you use.

## North star

**Universal core + N agent-adapters.** The core knows nothing about any specific tool. Each tool
(Claude Code, OpenCode, any future tool, …) plugs in via an adapter selected by config. Claude Code is
**not privileged** — it is simply the most capable adapter (all three capabilities native). The
backfill (one-shot population of a fresh KB from existing files) stays a first-class feature.

## The one separation that drives everything: trigger ⟂ compute ⟂ agent

These feel coupled but live on three independent axes:

- **Trigger** — what *notices* there is new raw to process. (hook / watchdog / poll)
- **Compute** — what *runs* the extract/cluster/synth. (local LM Studio / host subagents / cloud API)
- **Agent** — which host tool produced the history and consumes the memory.

Capture only **enqueues a pointer** (compute-free). Any processor then drains the queue. So using a
hook never locks you into cloud, and local models work behind any trigger. `config` picks each axis
independently.

### Key fact that makes capture cheap and lossless

A coding agent's transcript (e.g. Claude Code `.jsonl`) is an **append-only log of the whole
session**. Compaction shrinks the *context window*, not the *on-disk file*. Therefore:

- Reading the log later is **lossless** regardless of when you read it (no need to pre-empt compaction).
- **Capture is fundamentally a manifest-diff**: "which transcript files changed since last processed?"
  Hook, watchdog, and poll are three ways to *notice* that diff — identical in output, differing only
  in latency and how tool-specific they are.

## The three flows

| Flow | Mechanism | Agent-agnostic? | Model cost |
|---|---|---|---|
| **Inject** (memory → a starting session) | **must** be a per-agent shim — only the host can shape a session as it begins | no (always per-agent, thin, read-only) | none (file read) |
| **Capture** (notice new raw) | hook **or** watchdog **or** poll → **one shared inbox** | yes (watch/poll) or per-agent (hook) | none (enqueue only) |
| **Process** (raw → KB) | model-adapter: local / subscription / cloud | yes | local-free / subscription / $ |

## Three orthogonal seams

- **agent-adapter** — *where* transcripts live, *when* to capture, *how* to inject, *whether* the host
  offers compute. (WHERE / WHEN / inject / compute-availability)
- **input-handler** — *how* to read a given format → clean chunks. Self-discovering, source-agnostic. (HOW)
- **model-adapter** — *what* runs the LLM work: local / cloud / subscription. (COMPUTE)

Core loop, depending only on the seams:
`for each enabled agent-adapter → capture → input-handler(read) → model-adapter(process) → KB → inject`

## Agent-adapter = capability descriptor + thin shims

Each capability has a **native** impl (best) and a **universal fallback** (always works). A tool with
nothing native is still fully supported via fallbacks.

| Capability | native (if the tool has it) | universal fallback (always) |
|---|---|---|
| **Capture** | lifecycle hook (CC: SessionEnd / PreCompact → enqueue) | **watch/poll** the transcripts dir |
| **Inject** | start hook (CC: SessionStart `additionalContext`, scoped by cwd) | **render memory into a file the tool loads at startup** (CLAUDE.md / AGENTS.md / system prompt) |
| **Compute** | host doubles as an LLM backend (CC: subagents on the subscription) | **model-adapter** (local / cloud) |

### Adapters are a LIST → one shared KB

Enable several tools at once: **capture from all of them → one inbox → one KB → inject into all of
them.** Memory is cross-tool; `provenance` tags which tool/source each fact came from.

### `generic` adapter = escape hatch

Any tool the core has never heard of is supported purely declaratively — point it at a transcripts
folder and an inject file. This is the literal answer to "configurable for any tool."

## The subscription constraint (a property of ONE adapter, not the foundation)

A Claude Code subscription is a **flat-rate, ~unlimited Claude compute pool — but redeemable only from
inside a live, human-present session** (not headless; `claude -p` on a cron is the "automated CI-style
usage" gray zone). Consequences, all localized to `claude-code.compute = subagents`:

- The "engine" for that path is **Claude Code itself** (hooks + Workflow + subagents) — no server, no API key, no deploy.
- The real currency is **rate-limit budget, not dollars** (the "$50 backfill" was actually *included*).
- Processing is **opportunistic / piggyback**: drain the queue while you are already in a session
  (the `/loop` you run, or a SessionStart that kicks a background subagent) — never a true daemon.
- So **local models' real job** is not "cheaper" but **privacy + sparing the rate-limit budget + working when no session is open**.
- **Inject is free but a recurring tax**: it spends context tokens *every* session → keep it lean (index-first, pull detail on demand).

## Model-adapter interface

A backend runs **workers** against a task spec; heavy output goes to disk, only metadata returns.

```
run(task) -> result
  task = { phase, prompt, inputs:[absPath], output:absPath, schema|null, model }

capabilities = { tools: bool, structuredOutput: "schema"|"json"|"none", maxContextTokens: int }
```

| Backend | `run` is… | tools | Used for |
|---|---|---|---|
| **subscription** | Workflow `agent(prompt,{schema,model})` — no API key | ✅ | ingest (self-discovers format), cluster (scripts 6k atoms), extract, synth |
| **local** | POST `/chat/completions` to any auto-discovered OpenAI-compatible server (LM Studio / Ollama / llama.cpp / Jan), reusing a model already loaded, **plain JSON** + lenient-parse/retry (a3b path) | ❌ | per-unit extract & synth |
| **cloud** | Anthropic Messages / OpenAI-compatible, real tools + structured output | ⚙️ | any phase |

**Per-phase strategy** (from `config.model.<phase>`):
- backend has `tools` → delegate the *whole* phase (incl. scaffolding) to one tool-using worker;
- completion-only backend → orchestrator runs the **mechanical** scaffold (`input/chunk.py` for ingest,
  embedding-cluster for cluster — both already exist in `llmem/`) and calls the model **per unit**.

`config` makes "backfill on Sonnet, incremental on local a3b" a one-line flip. Model is never hardcoded.

## Repo structure (the seam layout)

```
memory-agent/
  core/        config.py · pipeline.md (on-disk contract) · prompts/ (route/merge/new-note rubrics)
  input/       handlers/ (format hints for tool-using ingest) · chunk.py (mechanical distill+chunk)
  adapters/
    agent/     adapter.md (capability contract) · claude_code/ (hooks, commands, launchd)
               · generic.py (any tool, zero code) — opencode resolves to generic until native
    model/     adapter.md · local.py (LM Studio) · cloud.py (API) · subscription.py (claude -p)
               · stub.py (tests)
  engine/      backfill.js (THE key feature) · refresh.py (atom collector) · merge.py + merge.js
               (consolidation: route → gate → re-synth touched notes; frontmatter built in code)
               · evals.py · capture.py · inject.py · launchd-refresh.sh
  knowledge/   the KB output: index.md + notes (git-ignored — personal)
  state/       gitignored: inbox/ derived/{atoms,merge}/ backups/ logs/ manifest.json
  config.json  active agents (list) · per-phase model backend · merge policy · paths
```

## config.json (shape)

```jsonc
{
  "agents": [
    { "adapter": "claude-code" },                     // all capabilities native: hooks + subagents
    { "adapter": "opencode" },                         // native where it has it, else fallback
    { "adapter": "generic", "name": "my-tool",
      "transcripts": { "dir": "~/.my-tool/logs", "format": "auto" },
      "inject": { "file": "~/.my-tool/memory.md" } }   // core knows nothing about it; fallbacks carry it
  ],
  "capture": "native+poll",                            // hooks where available + poll as backstop
  "inject":  { "scope": "index+project", "maxNotes": 12 },
  "model": {                                           // independent of the agent choice
    "ingest":  { "backend": "subscription", "model": "sonnet" },
    "extract": { "backend": "local",        "model": "qwen/qwen3.6-35b-a3b" },
    "cluster": { "backend": "subscription", "model": "sonnet" },
    "synth":   { "backend": "subscription", "model": "sonnet" },
    "lint":    { "backend": "local",        "model": "qwen/qwen3.6-35b-a3b" }
  },
  "backends": {
    "subscription": { "kind": "agent" },
    "local": { "kind": "completion",                   // auto-discovered: first healthy server wins.
               "discover": ["http://localhost:1234/v1",   // LM Studio
                            "http://localhost:11434/v1",   // Ollama
                            "http://localhost:8080/v1",    // llama.cpp
                            "http://localhost:1337/v1"] }, // Jan  (baseUrl, if set, is tried first)

    "cloud": { "kind": "completion", "baseUrl": "https://api.anthropic.com", "apiKeyEnv": "ANTHROPIC_API_KEY" }
  }
}
```

## Next build step

Build **two adapters at once** — `claude-code` (all capabilities native) + `generic` (pure fallback)
— over the existing backfill, proving universality at both poles. Order: write `core/pipeline.md` +
`adapters/agent/adapter.md` (capability contract), then scaffold the two adapters + `config.json`,
then wire `engine/refresh` as the `/loop` target draining the shared inbox.
