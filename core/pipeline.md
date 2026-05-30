# core/pipeline.md — the on-disk protocol

The core is source-, agent-, and model-agnostic. Everything it does is moving files through a fixed
on-disk contract. Any orchestrator (the `engine/backfill.js` Workflow, the local `llmem/` runner, or
the incremental `engine/refresh`) reads/writes these same paths, so they interoperate.

## Directories

```
knowledge/                 the KB output (committed or local-only): index.md + <slug>.md notes
state/                     gitignored runtime state
  inbox/pending.jsonl        capture worklist (one JSON record per line, append-only)
  manifest.json              per-adapter map of seen transcripts (mtime+size) — drives the diff
  derived/
    chunks/                  clean text chunks (ingest output)   <<source: ID chunk=N>> header
    atoms/<chunk>.json       atomic facts (extract output)
    clusters/NN-slug.json    deduped groups (cluster output)
    notes/<slug>.md          synthesized notes (synth output) → promoted to knowledge/
```

## Two flows + four phases

**Capture** (compute-free, always-on): manifest-diff over each adapter's transcripts dir → append new
sessions to `inbox/pending.jsonl`. Lossless — transcripts are append-only logs, so reading later loses
nothing. `baseline` mode records current files as seen WITHOUT enqueuing (backfill already covered them).

**Inject** (read-only, per-agent): build a lean payload from `knowledge/` (index-first; Read notes on
demand) and deliver it into a starting session (native hook) or a file the host loads (fallback).

**Process** (the four phases, run by an orchestrator via the model-adapter):

| Phase | Input | Output | Tool-capable backend | Completion-only fallback |
|---|---|---|---|---|
| ingest | raw files (or inbox) | `derived/chunks/*.txt` | agent self-discovers format + chunks | `input/chunk.py` (mechanical) |
| extract | one chunk | `derived/atoms/<chunk>.json` | agent per chunk | local model per chunk |
| cluster | all atoms | `derived/clusters/*.json` | agent scripts dedup at scale | `llmem/cluster.py` (embeddings) |
| synth | one cluster | `derived/notes/<slug>.md` → `knowledge/` | agent per cluster | local model per cluster |

## Record schemas

**inbox/pending.jsonl** (one per line):
```json
{"adapter":"claude-code","source":"<session-id>","abs":"/abs/path.jsonl","format":"claude-code-jsonl",
 "mtime":1733600000.0,"size":48211,"detected_at":1733600001.2,"via":"hook|poll"}
```
The drain step coalesces multiple records for the same `source` (keep the largest/latest).

**atom** (extract): `{claim, type, entities, evidence, source, confidence, tags}` — `type` ∈
{concept, claim, decision, entity, reference, task, user, project}; `source` = the session id.

**note** (synth): markdown with YAML frontmatter `{type, sources, confidence, links, conflicts,
provenance}` then a tight body. `provenance` records which adapter/tool the facts came from.

Provenance (`source`, `date`, adapter) is stamped in code, never trusted from the model.
