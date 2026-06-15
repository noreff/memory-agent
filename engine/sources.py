"""engine/sources — the registry of PLACES memory-agent indexes.

Two kinds of place feed ONE knowledge base:
  - adapters (config.agents): live tools captured every session (Claude Code, …)
  - registered folders (`mem.py sources add`): exports, doc dumps, old-machine archives — first-class
    too. They load as generic adapters, so the same capture/cycle pipeline re-scans them incrementally
    (a backfilled folder stops being a one-shot: new files in it get picked up forever after).

The registry is DECLARATIVE — it stores only the places (id/path/format/status). Per-place counts are
computed live from the manifest + atom store on read, so nothing here can drift from what was actually
processed. Stored at state/sources.json with atomic writes."""
from __future__ import annotations
import datetime
import json
import re
from pathlib import Path


def _path(cfg):
    return cfg.state_dir / "sources.json"


def load(cfg) -> list:
    p = _path(cfg)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("sources", [])
        except Exception:
            return []
    return []


def save(cfg, sources) -> None:
    p = _path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"sources": sources}, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)  # atomic — a torn registry must never silently lose a place


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")[:48] or "source"


def _config_adapter_names(cfg) -> set:
    names = set()
    for entry in cfg.agents:
        if entry.get("enabled") is False:
            continue
        names.add(entry.get("name")
                  or ("claude-code" if entry.get("adapter") == "claude-code"
                      else entry.get("adapter", "generic")))
    return names


def as_adapter_entries(cfg) -> list:
    """Registered folders → config.agents-shaped entries, so load_adapters can build GenericAdapters
    for them. Disabled sources are skipped."""
    out = []
    for rec in load(cfg):
        if rec.get("status") == "disabled":
            continue
        out.append({"adapter": "generic", "name": rec["id"],
                    "transcripts": {"dir": rec["path"], "format": rec.get("format", "auto")}})
    return out


def add(cfg, path, fmt="auto", id=None, kind="generic", status="active") -> dict:
    path = str(Path(path).expanduser())
    sid = _slug(id or Path(path).name)
    if sid in _config_adapter_names(cfg):
        raise ValueError(f"id '{sid}' clashes with a config adapter — pass a distinct --id")
    sources = load(cfg)
    if any(s["id"] == sid for s in sources):
        raise ValueError(f"source id '{sid}' is already registered (pass a distinct --id)")
    rec = {"id": sid, "kind": kind, "path": path, "format": fmt,
           "status": status, "added": datetime.date.today().isoformat()}
    sources.append(rec)
    save(cfg, sources)
    return rec


def remove(cfg, id) -> bool:
    sources = load(cfg)
    kept = [s for s in sources if s["id"] != id]
    if len(kept) == len(sources):
        return False
    save(cfg, kept)
    return True


# ── live stats (computed on read, never stored → cannot drift) ───────────────
def _manifest(cfg) -> dict:
    p = cfg.manifest_path
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _done(cfg) -> list:
    p = cfg.state_dir / "inbox" / "done.jsonl"
    out = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def _atoms_by_source(cfg) -> dict:
    d = cfg.state_dir / "derived" / "atoms"
    out = {}
    if d.exists():
        for f in d.glob("*.json"):
            try:
                arr = json.loads(f.read_text(encoding="utf-8"))
                out[f.stem] = len(arr) if isinstance(arr, list) else 0
            except Exception:
                out[f.stem] = 0
    return out


def stats_for(name, manifest, done, atoms_src) -> dict:
    """Live counters for one place, keyed by its adapter/source name."""
    files = len(manifest.get(name, {}))
    place_sources = {r.get("source") for r in done
                     if r.get("adapter") == name and r.get("source")}
    atoms = sum(atoms_src.get(s, 0) for s in place_sources)
    times = [r.get("refreshed_at") or r.get("detected_at") for r in done
             if r.get("adapter") == name and (r.get("refreshed_at") or r.get("detected_at"))]
    last = datetime.date.fromtimestamp(max(times)).isoformat() if times else "—"
    return {"files": files, "captured": len(place_sources), "atoms": atoms, "last": last}


def places(cfg) -> dict:
    """The whole `mem.py sources` view: where OUTPUTS resolve to (surfaces the CACHE/DATA class of
    split at a glance) + every INPUT place (adapters + registered folders) with live stats."""
    manifest, done, atoms_src = _manifest(cfg), _done(cfg), _atoms_by_source(cfg)
    rows, seen = [], set()
    for entry in cfg.agents:
        if entry.get("enabled") is False:
            continue
        name = entry.get("name") or ("claude-code" if entry.get("adapter") == "claude-code"
                                     else entry.get("adapter", "generic"))
        t = entry.get("transcripts", {}) or {}
        path = t.get("dir") or ("~/.claude/projects"
                                if entry.get("adapter") == "claude-code" else "—")
        rows.append({"id": name, "kind": "adapter", "path": path,
                     "format": t.get("format", "auto"), "status": "active",
                     **stats_for(name, manifest, done, atoms_src)})
        seen.add(name)
    for rec in load(cfg):
        if rec["id"] in seen:
            continue
        rows.append({**rec, **stats_for(rec["id"], manifest, done, atoms_src)})
    notes = 0
    if cfg.knowledge_dir.exists():
        notes = sum(1 for p in cfg.knowledge_dir.glob("*.md")
                    if p.name not in ("index.md", "README.md"))
    return {"knowledge": str(cfg.knowledge_dir), "state": str(cfg.state_dir),
            "notes": notes, "sources": rows}
