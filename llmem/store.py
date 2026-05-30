"""Manifest + provenance helpers. Provenance (source id, date) is derived in CODE
and never trusted from the model output."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path

from config import MANIFEST


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {"processed": {}}


def save_manifest(m: dict) -> None:
    MANIFEST.write_text(json.dumps(m, indent=2), encoding="utf-8")


def source_id(filename: str) -> str:
    """myproject__078e8ddf-....md -> claude-code/078e8ddf-..."""
    stem = Path(filename).stem
    if "__" in stem:
        stem = stem.split("__", 1)[1]
    return f"claude-code/{stem}"


def convo_date(text: str) -> str:
    """Pull the session start date (yyyy-mm-dd) from the transcript header."""
    m = re.search(r"Started\s*\|\s*(\d{4}-\d{2}-\d{2})", text)
    if not m:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    return m.group(1) if m else "unknown"
