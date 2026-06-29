"""Resolve config.agents[] → concrete AgentAdapter instances (enabled only).
Unknown adapters fall back to GenericAdapter so any tool is supported by config alone."""
from __future__ import annotations

from .claude_code.adapter import ClaudeCodeAdapter
from .generic import GenericAdapter

REGISTRY = {
    "claude-code": ClaudeCodeAdapter,
    "generic": GenericAdapter,
    # "opencode": OpenCodeAdapter,  # → GenericAdapter until a native one is written
}


def load_adapters(cfg):
    out = []
    for entry in cfg.agents:
        if entry.get("enabled") is False:
            continue
        cls = REGISTRY.get(entry.get("adapter", "generic"), GenericAdapter)
        out.append(cls.from_config(entry))
    # registered folders (mem.py sources add) are first-class sources: they load as generic adapters
    # so the same capture/cycle pipeline re-scans them incrementally. (lazy import avoids a cycle)
    from engine.sources import as_adapter_entries
    for entry in as_adapter_entries(cfg):
        out.append(GenericAdapter.from_config(entry))
    return out
