"""Stub backend — deterministic canned output for testing pipeline wiring without a live model."""
from __future__ import annotations
import json

from .base import ModelBackend, Result, Task


class StubBackend(ModelBackend):
    name = "stub"

    def run(self, task: Task) -> Result:
        if task.phase == "extract":
            return Result(text=json.dumps({"atoms": [
                {"claim": "Stub fact extracted for testing.", "type": "project",
                 "entities": ["stub-entity"], "confidence": "low", "tags": ["stub"]}]}))
        if task.phase == "merge":
            return Result(text="# Stub note\n\nMerged stub content for testing.\n===CONFLICTS===\nnone")
        return Result(text="stub")
