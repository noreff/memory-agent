"""Capture = manifest-diff over an adapter's transcripts dir → the shared inbox. Compute-free.

Lossless: transcripts are append-only logs (compaction shrinks context, not the file), so reading
later loses nothing. ``baseline=True`` records current files as seen WITHOUT enqueuing — the backfill
already covered them, so capture should only fire on sessions that appear AFTER the baseline."""
from __future__ import annotations
import json
import time
from pathlib import Path


def _load_manifest(p: Path) -> dict:
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_manifest(p: Path, manifest: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _append_inbox(inbox: Path, records: list) -> None:
    inbox.parent.mkdir(parents=True, exist_ok=True)
    with inbox.open("a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


def _record(adapter, path: Path, sig: dict, via: str) -> dict:
    return {
        "adapter": adapter.name, "source": adapter.source_id(path), "abs": str(path),
        "format": adapter.fmt, "mtime": sig["mtime"], "size": sig["size"],
        "detected_at": time.time(), "via": via,
    }


def capture(adapter, cfg, baseline: bool = False) -> dict:
    """Scan the adapter's transcripts dir; enqueue new/changed capturable files (unless baseline)."""
    manifest = _load_manifest(cfg.manifest_path)
    seen = manifest.setdefault(adapter.name, {})
    new, scanned = [], 0
    for path in adapter.iter_transcripts():
        scanned += 1
        try:
            st = path.stat()
        except OSError:
            continue
        key = str(path)
        sig = {"mtime": st.st_mtime, "size": st.st_size}
        prev = seen.get(key)
        if prev and prev.get("mtime") == sig["mtime"] and prev.get("size") == sig["size"]:
            continue  # unchanged — already processed
        seen[key] = sig  # record the new signature (even if we skip enqueuing it)
        if baseline or not adapter.is_capturable(path):
            continue
        new.append(_record(adapter, path, sig, via="poll"))
    _save_manifest(cfg.manifest_path, manifest)
    if new:
        _append_inbox(cfg.inbox, new)
    return {"adapter": adapter.name, "scanned": scanned, "enqueued": len(new), "baseline": baseline}


def enqueue_path(adapter, cfg, abs_path: str, via: str = "hook") -> dict:
    """Enqueue a single known transcript (the hook path — no glob needed). Idempotent by mtime+size."""
    p = Path(abs_path)
    if not p.exists():
        return {"enqueued": 0, "reason": "missing"}
    if not adapter.is_capturable(p):
        return {"enqueued": 0, "reason": "skipped"}
    manifest = _load_manifest(cfg.manifest_path)
    seen = manifest.setdefault(adapter.name, {})
    st = p.stat()
    key, sig = str(p), {"mtime": st.st_mtime, "size": st.st_size}
    prev = seen.get(key)
    if prev and prev.get("mtime") == sig["mtime"] and prev.get("size") == sig["size"]:
        return {"enqueued": 0, "reason": "unchanged"}
    seen[key] = sig
    _save_manifest(cfg.manifest_path, manifest)
    _append_inbox(cfg.inbox, [_record(adapter, p, sig, via=via)])
    return {"enqueued": 1, "source": adapter.source_id(p)}
