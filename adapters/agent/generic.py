"""Generic adapter: pure-fallback. Any tool, configured declaratively — no host-native anything.
The literal answer to 'configurable for any tool': point it at a transcripts dir + an inject file."""
from __future__ import annotations
import hashlib
from pathlib import Path

from .base import AgentAdapter


class GenericAdapter(AgentAdapter):
    cap_capture = "poll"
    cap_inject = "file"
    cap_compute = None
    glob = "*"  # unknown format → match everything; the input-handler self-discovers per file

    def source_id(self, path: Path) -> str:
        # arbitrary folders can repeat stems across subdirs (notes.md everywhere) — disambiguate,
        # or two files would coalesce into one inbox record and share one atoms file
        h = hashlib.sha1(str(path).encode()).hexdigest()[:6]
        return f"{path.stem}-{h}"
