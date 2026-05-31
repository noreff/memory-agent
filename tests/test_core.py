"""Minimal core tests: the behaviors that protect user data. Run: python3 -m unittest discover tests"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config as cfgmod              # noqa: E402
from core.config import SENTINEL               # noqa: E402
from engine import merge as M                  # noqa: E402
from engine.capture import capture             # noqa: E402
from engine.refresh import refresh             # noqa: E402
from input.chunk import distill                # noqa: E402
from adapters.agent.generic import GenericAdapter  # noqa: E402
from adapters.model.stub import StubBackend    # noqa: E402


def _cfg(d, **merge):
    return cfgmod.Config({"paths": {"knowledge": str(d / "kb"), "state": str(d / "state")},
                          "agents": [], "merge": merge or {"newNoteThreshold": 3}}, d)


class TestFrontmatter(unittest.TestCase):
    def test_flow_lists_roundtrip(self):
        note = "---\ntype: project\nsources: [a, b]\nlinks: []\n---\n\n# T\n\nBody.\n"
        fields, body = M.parse_note(note)
        self.assertEqual(M.fget(fields, "sources"), ("list", ["a", "b"]))
        self.assertEqual(M.fget(fields, "links"), ("list", []))
        rebuilt = M.build_note(fields, body)
        self.assertIn("  - a", rebuilt)
        self.assertIn("# T", rebuilt)

    def test_block_and_list_roundtrip(self):
        note = ("---\ntype: user\nsources:\n  - s1\nconflicts: |\n  old vs new\n---\n\nBody.\n")
        fields, body = M.parse_note(note)
        self.assertEqual(M.fget(fields, "conflicts"), ("block", "old vs new"))
        self.assertIn("conflicts: |", M.build_note(fields, body))

    def test_sanitize_keeps_horizontal_rules(self):
        hr = "Intro.\n\n---\n\nAfter the rule."
        self.assertEqual(M.sanitize_body(hr), hr)

    def test_sanitize_strips_model_frontmatter(self):
        self.assertEqual(M.sanitize_body("---\ntype: x\nfoo: y\n---\nReal."), "Real.")


class TestGate(unittest.TestCase):
    def test_unknown_verdicts_never_discard(self):
        plan = M.gate([{"id": "a#1", "verdict": "into", "target": "n"},
                       {"id": "a#2", "verdict": "banana"},
                       {"id": "a#3", "verdict": "discard"},
                       {"verdict": "into", "target": "n"}], {}, 3)
        self.assertEqual(plan["discard"], ["a#3"])
        self.assertEqual(len(plan["into"]["n"]), 1)

    def test_threshold_gates_new_topics(self):
        ds = [{"id": f"a#{i}", "verdict": "new", "topic": "t", "type": "project"}
              for i in range(2)]
        plan = M.gate(ds, {}, 3)
        self.assertIn("t", plan["pending_add"])
        plan = M.gate(ds, {"t": {"atoms": [{"id": "old#1"}]}}, 3)  # pool atom graduates it
        self.assertIn("t", plan["new"])


class TestCapture(unittest.TestCase):
    def test_first_run_is_always_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "raw").mkdir()
            (d / "raw" / "s1.txt").write_text("hello")
            cfg = _cfg(d)
            a = GenericAdapter.from_config({"adapter": "generic", "name": "t",
                                            "transcripts": {"dir": str(d / "raw")}})
            r = capture(a, cfg)  # no manifest yet -> forced baseline
            self.assertTrue(r["baseline"] and r["firstRun"])
            self.assertEqual(r["enqueued"], 0)
            (d / "raw" / "s2.txt").write_text("new session")
            r = capture(a, cfg)
            self.assertEqual(r["enqueued"], 1)
            self.assertEqual(capture(a, cfg)["enqueued"], 0)  # idempotent


class TestDistill(unittest.TestCase):
    def test_echo_suppression(self):
        with tempfile.TemporaryDirectory() as td:
            tx = Path(td) / "t.jsonl"
            recs = [
                {"type": "user", "isSidechain": False, "message": {"role": "user", "content":
                 "<system-reminder>injected memory about ports</system-reminder>real: use pnpm"}},
                {"type": "user", "isSidechain": False,
                 "message": {"role": "user", "content": f"{SENTINEL} internal route prompt"}},
                {"type": "assistant", "isSidechain": True,
                 "message": {"role": "assistant", "content": "subagent noise"}},
            ]
            tx.write_text("\n".join(json.dumps(r) for r in recs))
            text, _, _ = distill(str(tx), "claude-code-jsonl")
            self.assertIn("use pnpm", text)
            for leaked in ("injected memory", "internal route prompt", "subagent noise"):
                self.assertNotIn(leaked, text)


class TestRefresh(unittest.TestCase):
    def test_routed_marks_survive_reextraction(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            cfg = _cfg(d)
            (cfg.state_dir / "inbox").mkdir(parents=True)
            tx = d / "s1.jsonl"
            tx.write_text(json.dumps({"type": "user", "isSidechain": False, "message":
                                      {"role": "user", "content": "redis on port 6380"}}))
            rec = {"adapter": "x", "source": "s1", "abs": str(tx),
                   "format": "claude-code-jsonl", "size": 100, "mtime": time.time()}
            cfg.inbox.write_text(json.dumps(rec) + "\n")
            refresh(cfg, StubBackend(), log=lambda m: None)
            af = cfg.state_dir / "derived" / "atoms" / "s1.json"
            atoms = json.loads(af.read_text())
            atoms[0]["routed"] = {"to": "note-x", "at": "2026-01-01"}
            af.write_text(json.dumps(atoms))
            cfg.inbox.write_text(json.dumps({**rec, "size": 200}) + "\n")
            refresh(cfg, StubBackend(), log=lambda m: None)
            self.assertEqual(json.loads(af.read_text())[0]["routed"]["to"], "note-x")

            # tail mode: append a NEW record → only the tail is extracted, appended; mark intact
            with tx.open("a") as f:
                f.write("\n" + json.dumps({"type": "user", "isSidechain": False, "message":
                                           {"role": "user", "content": "also uses caddy on 8080"}}))
            cfg.inbox.write_text(json.dumps({**rec, "size": 300, "mtime": time.time()}) + "\n")
            refresh(cfg, StubBackend(), log=lambda m: None)
            atoms = json.loads(af.read_text())
            self.assertEqual(len(atoms), 2)                       # appended, not rewritten
            self.assertEqual(atoms[0]["routed"]["to"], "note-x")  # mark untouched
            self.assertNotIn("routed", atoms[1])                  # new atom unrouted


if __name__ == "__main__":
    unittest.main()
