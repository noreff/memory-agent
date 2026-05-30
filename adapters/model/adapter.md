# adapters/model/adapter.md — the model-adapter contract

A model backend runs a `Task` (one unit of LLM work) and returns a `Result`. The orchestrator picks a
backend **per phase** from `config.model.<phase>` — model is never hardcoded.

## Interface (`base.py`)

```python
@dataclass
class Task:   phase, prompt, system="", max_tokens, temperature, expect_json=False, model=None
@dataclass
class Result: text, finish, backend

class ModelBackend:
    name; tools: bool; structured_output: "schema"|"json"|"none"
    run(task) -> Result
```

## Backends

| name | `run` is… | tools | needs |
|---|---|---|---|
| `local` | LM Studio `/chat/completions`, **plain JSON** (never json_schema — MLX loops) | no | LM Studio on `:1234` |
| `cloud` | Anthropic Messages API (or OpenAI-compatible via `baseUrl`) | no | API key in `apiKeyEnv` ($) |
| `subscription` | headless `claude -p` — no key, in-session use only (DESIGN 'прикол') | yes | `claude` CLI |
| `stub` | canned output | no | — (testing) |

## Strategy per phase

- **tool-capable** backend (subscription) → can own a whole phase (ingest self-discovers format,
  cluster scripts atoms at scale). For bulk backfill use `engine/backfill.js` (the Workflow), which is
  the richest realization of this path.
- **completion-only** backend (local/cloud) → the orchestrator supplies the **mechanical scaffold**
  (`input/chunk.py` for ingest, heuristic/embedding routing for cluster) and calls the model only for
  per-unit `extract`/`synth`. This is what `engine/refresh.py` does.

## Selecting

```python
from adapters.model.loader import backend_for_phase
b = backend_for_phase(cfg, "extract")          # from config.model.extract
b = backend_for_phase(cfg, "extract", "stub")  # explicit override
b.run(Task(phase="extract", system=SYS, prompt=text, expect_json=True))
```
