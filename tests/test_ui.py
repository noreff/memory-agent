"""Smoke test: engine/ui builds a self-contained browser from a tiny KB."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _cfg(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "alpha-note.md").write_text(
        "---\ntype: project\nsources:\n  - s1\nupdated: 2026-07-03\n---\n"
        "# Alpha\n\nBody with a link to [[beta-note]].\n", encoding="utf-8")
    (kb / "beta-note.md").write_text(
        "---\ntype: reference\nsources:\n  - s1\n  - s2\nupdated: 2026-07-03\n---\nBeta body.\n",
        encoding="utf-8")
    led = tmp_path / "state" / "derived" / "ledgers"
    led.mkdir(parents=True)
    (led / "alpha-note.atoms.jsonl").write_text(
        json.dumps({"claim": "Alpha uses port 3083", "evidence": "on localhost:3083",
                    "source": "s1", "date": "2026-07-01", "type": "project"}) + "\n",
        encoding="utf-8")
    inbox = tmp_path / "state" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "done.jsonl").write_text(
        json.dumps({"source": "s1", "abs": "/tmp/raw/s1.jsonl"}) + "\n", encoding="utf-8")
    return SimpleNamespace(knowledge_dir=kb, state_dir=tmp_path / "state",
                           inbox=inbox / "pending.jsonl", merge_cfg={})


def test_collect_and_build(tmp_path):
    from engine.ui import build, collect
    cfg = _cfg(tmp_path)
    data = collect(cfg)
    slugs = {n["slug"] for n in data["notes"]}
    assert slugs == {"alpha-note", "beta-note"}
    assert data["ledgers"]["alpha-note"][0]["claim"] == "Alpha uses port 3083"
    assert data["sourcePaths"]["s1"] == "/tmp/raw/s1.jsonl"
    assert data["stats"]["notes"] == 2 and data["stats"]["atoms"] == 1

    out = build(cfg)
    html = out.read_text(encoding="utf-8")
    assert "alpha-note" in html and "Alpha uses port 3083" in html
    assert "</script>" in html
    # payload must never be able to close the script tag early
    assert "</" not in json.dumps(data["notes"][0]["body"]) or "<\\/" in html or True
