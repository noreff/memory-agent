"""Model-adapter contract. A backend runs a Task (a unit of LLM work) and returns a Result. The
orchestrator picks a backend per phase from config.model.<phase>; tool-capable backends can own a
whole phase, completion-only backends get a mechanical scaffold + per-unit calls. See adapter.md."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class Task:
    phase: str                 # ingest | extract | cluster | synth | lint
    prompt: str
    system: str = ""
    max_tokens: int = 2000
    temperature: float = 0.2
    expect_json: bool = False  # hint; the caller parses leniently
    model: str | None = None   # backend-specific id; falls back to backend default
    extra: dict | None = None  # backend-specific payload extras (e.g. anti-repeat penalties)


@dataclass
class Result:
    text: str
    finish: str = "stop"       # "stop" | "length" | ...
    backend: str = ""


class ModelBackend:
    name = "base"
    tools = False                  # can it self-discover format / write helper scripts?
    structured_output = "none"     # "schema" | "json" | "none"

    def run(self, task: Task) -> Result:
        raise NotImplementedError
