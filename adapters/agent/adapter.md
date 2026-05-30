# adapters/agent/adapter.md — the agent-adapter capability contract

An agent-adapter plugs a host tool (Claude Code, OpenCode, any future tool, …) into the universal core.
The core depends ONLY on this contract, never on a specific tool. Each capability has a **native**
implementation (best, if the tool offers it) and a **universal fallback** (always works).

## Capabilities

| Capability | what it does | native | universal fallback |
|---|---|---|---|
| `cap_capture` | notice new transcripts | `"hook"` (lifecycle event → enqueue) | `"watch"` / `"poll"` the transcripts dir |
| `cap_inject` | surface memory into a session | `"hook"` (host injects context at start) | `"file"` (render to a file the host loads) |
| `cap_compute` | host doubles as an LLM backend | `"subagents"` (e.g. on a subscription) | `None` → use the model-adapter |

Capture only ever **enqueues a pointer** — it runs no model — so choosing a hook never couples you to
cloud compute. Inject is read-only. Compute is selected separately in `config.model.<phase>`.

## Interface (`adapters/agent/base.py`)

```python
class AgentAdapter:
    cap_capture, cap_inject, cap_compute   # capability declaration
    glob = "*.jsonl"                       # transcript glob under transcripts_dir

    iter_transcripts() -> Iterator[Path]   # capture: enumerate candidate files (universal)
    is_capturable(path) -> bool            # filter host noise (default True; CC skips sidechains)
    source_id(path) -> str                 # stable id for a transcript (default: stem)
    deliver_inject(payload, stdin) -> None # native: emit host JSON; fallback: write inject_file
    from_config(entry) -> AgentAdapter     # build from a config.agents[] entry
```

## Adapters are a LIST → one shared KB

`config.agents` is a list; enable several at once. Capture from all of them lands in one inbox; the KB
is the union; inject goes back into all of them. `provenance` on each note records the source tool.

## The three concrete adapters

| adapter | capture | inject | compute | notes |
|---|---|---|---|---|
| `claude-code` | hook (+poll) | hook | subagents | all native. Skips `isSidechain:true` transcripts. Subscription compute is in-session only. |
| `generic` | poll | file | None | pure fallback; any tool by `{transcripts.dir, format, inject.file}`. |
| `opencode` | (tbd) | (tbd) | (tbd) | falls back to `generic` until written. |

## Adding a new tool

Either (a) drop a `{"adapter":"generic","name":"X","transcripts":{...},"inject":{"file":...}}` entry in
`config.json` — zero code — or (b) write a subclass overriding only the host-specific bits
(`is_capturable`, `deliver_inject`) when the tool has native hooks/compute worth using.
