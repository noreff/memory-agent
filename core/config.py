"""Central config for memory-agent. Stdlib-only. Reads config.json at the repo root and resolves
paths (``~`` and relative-to-root). This is the top-level orchestration config; ``llmem/config.py``
remains the local-model backend's own config."""
from __future__ import annotations
import json
import os
from pathlib import Path

# Marks the system's OWN model calls (route/merge prompts). Transcripts containing it are skipped
# by capture, and text blocks containing it are stripped in distill — so internal processing run
# through a CLI session (e.g. `claude -p`) can never be re-memorized as user history.
SENTINEL = "<<memory-agent:internal-task>>"


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
        data_home = os.environ.get("MEMORY_AGENT_DATA")
        if data_home:  # plugin installs: code lives in an ephemeral cache, data must live elsewhere
            base = Path(os.path.expanduser(data_home))
            self.knowledge_dir = base / "knowledge"
            self.state_dir = base / "state"
        else:
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


# ── spaces: separate knowledge bases (e.g. personal vs work) sharing one pipeline ────────────────
# config.json: {"spaces": {"work": {"match": ["*vana*"], "knowledge": "knowledge-work",
#                                   "inject": {"files": [...]}},
#               "default": {"match": ["*"]}}}
# Atoms are stamped with their space at extract time (by transcript path); ingest groups by stamp
# and each space gets its own knowledge dir, pending pool, note ledgers, index and inject targets.
# With no spaces configured there is a single "default" space and behavior is unchanged.
def spaces(cfg) -> list:
    names = list((cfg.data.get("spaces") or {}).keys())
    if "default" not in names:
        names.append("default")
    return names


def space_of(cfg, path) -> str:
    """First configured space whose any glob matches the transcript path; 'default' otherwise."""
    import fnmatch
    p = str(path)
    for name, sc in (cfg.data.get("spaces") or {}).items():
        if name == "default":
            continue
        for pat in (sc.get("match") or []):
            if fnmatch.fnmatch(p, pat):
                return name
    return "default"


def for_space(cfg, name: str) -> "Config":
    """A shallow view of cfg scoped to one space: swapped knowledge dir / inject targets, plus a
    .space attr that space-aware path helpers (merge_dir, notes_state_dir) key off."""
    import copy
    view = copy.copy(cfg)
    view.space = name
    if name != "default":
        sc = (cfg.data.get("spaces") or {}).get(name, {})
        view.knowledge_dir = _resolve(sc.get("knowledge", f"knowledge-{name}"),
                                      cfg.knowledge_dir.parent)
        view.inject_cfg = sc.get("inject", {**cfg.inject_cfg, "files": []})
    return view


def load(path: Path | None = None) -> Config:
    root = repo_root()
    cfg_path = Path(path) if path else (root / "config.json")
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    # A config.json in the data dir overrides the shipped one (top-level keys win) — this is how
    # plugin installs customize models/policy without editing the update-overwritten plugin cache.
    data_home = os.environ.get("MEMORY_AGENT_DATA")
    if data_home:
        user_cfg = Path(os.path.expanduser(data_home)) / "config.json"
        if user_cfg.exists():
            try:
                data = {**data, **json.loads(user_cfg.read_text(encoding="utf-8"))}
            except Exception:
                pass  # a broken override must not take memory down; shipped config still works
    return Config(data, root)
