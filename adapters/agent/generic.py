"""Generic adapter: pure-fallback. Any tool, configured declaratively — no host-native anything.
The literal answer to 'configurable for any tool': point it at a transcripts dir + an inject file."""
from __future__ import annotations
from .base import AgentAdapter


class GenericAdapter(AgentAdapter):
    cap_capture = "poll"
    cap_inject = "file"
    cap_compute = None
    glob = "*"  # unknown format → match everything; the input-handler self-discovers per file
