"""Central config for memory-agent. Stdlib-only. Reads config.json at the repo root and resolves
paths (``~`` and relative-to-root). This is the top-level orchestration config; ``llmem/config.py``
remains the local-model backend's own config."""
from __future__ import annotations
import json
import os
from pathlib import Path


def repo_root() -> Path:
    """Walk up from this file until a directory containing config.json is found."""
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / "config.json").exists():
            return parent
    return here.parent.parent  # fallback: repo root is one above core/


def _resolve(path: str, base: Path) -> Path:
    p = Path(os.path.expanduser(path))
    return p if p.is_absolute() else (base / p)


class Config:
    def __init__(self, data: dict, root: Path):
        self.data = data
        self.root = root
        paths = data.get("paths", {})
        self.knowledge_dir = _resolve(paths.get("knowledge", "knowledge"), root)
        self.state_dir = _resolve(paths.get("state", "state"), root)
        self.agents = data.get("agents", [])
        self.capture_mode = data.get("capture", "native+poll")
        self.inject_cfg = data.get("inject", {"scope": "index+project", "maxNotes": 12})
        self.model = data.get("model", {})
        self.backends = data.get("backends", {})
        self.merge_cfg = data.get("merge", {})  # {autoPromote, newNoteThreshold}

    @property
    def inbox(self) -> Path:
        return self.state_dir / "inbox" / "pending.jsonl"

    @property
    def manifest_path(self) -> Path:
        return self.state_dir / "manifest.json"


def load(path: Path | None = None) -> Config:
    root = repo_root()
    cfg_path = Path(path) if path else (root / "config.json")
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    return Config(data, root)
