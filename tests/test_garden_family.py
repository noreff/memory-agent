"""Tests for the two family self-heal mechanisms: the birth guard (pure code) and the family
judge's bookkeeping (no model calls — the judge itself is exercised by live runs)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.ingest import family_guard  # noqa: E402


VALID = {"data-pipe", "local-ai-stack", "local-ai-youtube-channel", "vana-storage"}


def _new(topic):
    return {"id": "a#1", "verdict": "new", "topic": topic, "type": "project"}


def test_extension_redirects_into_existing_parent():
    out, n = family_guard([_new("data-pipe-api")], VALID)
    assert n == 1
    assert out[0]["verdict"] == "into" and out[0]["target"] == "data-pipe"


def test_exact_existing_slug_redirects():
    out, n = family_guard([_new("Data Pipe")], VALID)  # slugifies to data-pipe
    assert n == 1 and out[0]["target"] == "data-pipe"


def test_contraction_redirects_into_single_extension():
    out, n = family_guard([_new("vana-storage-api")], VALID)
    assert n == 1 and out[0]["target"] == "vana-storage"


def test_ambiguous_contraction_left_alone():
    # 'local-ai' relates to BOTH local-ai-stack and local-ai-youtube-channel — must not guess
    out, n = family_guard([_new("local-ai")], VALID)
    assert n == 0
    assert out[0]["verdict"] == "new" and out[0]["topic"] == "local-ai"


def test_unrelated_topic_untouched():
    out, n = family_guard([_new("ffmpeg-filters")], VALID)
    assert n == 0 and out[0]["verdict"] == "new"


def test_shared_words_without_hyphen_boundary_untouched():
    # 'data-pipeline' is NOT an extension of 'data-pipe' at a hyphen boundary
    out, n = family_guard([_new("data-pipeline")], VALID)
    assert n == 0 and out[0]["verdict"] == "new"


def test_non_new_decisions_pass_through():
    d = {"id": "a#2", "verdict": "into", "target": "data-pipe"}
    out, n = family_guard([d], VALID)
    assert n == 0 and out[0] == d


def test_judged_state_reopens_on_membership_change(tmp_path):
    from types import SimpleNamespace
    from engine import garden as G
    cfg = SimpleNamespace(state_dir=tmp_path, knowledge_dir=tmp_path / "kb", merge_cfg={})
    (tmp_path / "kb").mkdir()
    for s in ("data-pipe.md", "data-pipe-api.md"):
        (tmp_path / "kb" / s).write_text("---\ntype: project\n---\nbody\n", encoding="utf-8")
    fams = G.unjudged_families(cfg)
    assert "data-pipe" in fams
    G._record_verdict(cfg, "data-pipe", fams["data-pipe"], "keep")
    assert "data-pipe" not in G.unjudged_families(cfg)  # verdict remembered
    (tmp_path / "kb" / "data-pipe-project.md").write_text(
        "---\ntype: project\n---\nbody\n", encoding="utf-8")
    assert "data-pipe" in G.unjudged_families(cfg)  # new member reopens the question
