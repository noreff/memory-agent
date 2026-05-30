"""Single-writer lock for pipeline cycles. The realistic collision: the launchd extractor fires
while a session runs /memory-refresh (or two sessions run it). Writers take the lock; whoever
loses skips the cycle gracefully — atoms/inbox are append-safe and the next cycle picks up.

POSIX flock; on platforms without fcntl (Windows) it degrades to a no-op (atomic writes still
protect against torn files)."""
from __future__ import annotations
from contextlib import contextmanager

try:
    import fcntl
except ImportError:  # Windows — best-effort mode
    fcntl = None


class Busy(Exception):
    """Another pipeline cycle holds the lock."""


@contextmanager
def pipeline_lock(cfg, name="pipeline"):
    if fcntl is None:
        yield
        return
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.state_dir / f".{name}.lock"
    f = path.open("w")
    try:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Busy(f"another {name} cycle is running (lock: {path})")
        yield
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        finally:
            f.close()
