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
from adapters.model import loader as L           # noqa: E402
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


class TestSources(unittest.TestCase):
    def test_register_loads_as_adapter_and_lists(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "exp").mkdir()
            cfg = _cfg(d)
            from engine import sources as S
            from adapters.agent.loader import load_adapters
            rec = S.add(cfg, str(d / "exp"), fmt="auto", id="myexport")
            self.assertEqual(rec["id"], "myexport")
            self.assertEqual([s["id"] for s in S.load(cfg)], ["myexport"])
            # a registered folder surfaces as a generic adapter → captured by the same pipeline
            self.assertIn("myexport", [a.name for a in load_adapters(cfg)])
            # places() lists it with the output location surfaced (catches CACHE/DATA-style splits)
            info = S.places(cfg)
            self.assertEqual(info["sources"][0]["id"], "myexport")
            self.assertTrue(info["knowledge"].endswith("kb"))
            # duplicate id is rejected; removal is clean
            with self.assertRaises(ValueError):
                S.add(cfg, str(d / "exp"), id="myexport")
            self.assertTrue(S.remove(cfg, "myexport"))
            self.assertEqual(S.load(cfg), [])
class TestLocalDiscovery(unittest.TestCase):
    """Backend auto-discovery + model resolution. urllib is never touched: we monkeypatch
    list_models so the tests need no real server."""

    def _cfg(self, local=None, extract_local=None):
        backends = {"local": local} if local is not None else {}
        model = {"extract": {"backend": "auto",
                             "models": ({"local": extract_local} if extract_local else {})}}
        return cfgmod.Config({"paths": {"knowledge": "k", "state": "s"},
                              "agents": [], "model": model, "backends": backends}, Path("/tmp"))

    def _patch(self, healthy: dict):
        """healthy: {base_url: [model_ids]} — every other URL probes as down ([])."""
        orig = L.list_models
        L.list_models = lambda base, timeout=1.5: list(healthy.get(base.rstrip("/"), []))
        self.addCleanup(lambda: setattr(L, "list_models", orig))

    def test_discovery_picks_first_healthy_url(self):
        # LM Studio (:1234) down, Ollama (:11434) up → discovery skips to the healthy one.
        self._patch({"http://localhost:11434/v1": ["llama3:8b"]})
        cfg = self._cfg(local={})  # uses DEFAULT_DISCOVER order
        base, models = L.probe_local(cfg)
        self.assertEqual(base, "http://localhost:11434/v1")
        self.assertEqual(models, ["llama3:8b"])
        self.assertTrue(L.local_reachable(cfg))
        self.assertEqual(L.detect_backend(cfg), "local")

    def test_baseurl_is_tried_first_for_backcompat(self):
        # A configured baseUrl wins over the default discover list even if a default is also up.
        self._patch({"http://localhost:9999/v1": ["custom-model"],
                     "http://localhost:1234/v1": ["lmstudio-model"]})
        cfg = self._cfg(local={"baseUrl": "http://localhost:9999/v1"})
        self.assertEqual(L.probe_local(cfg)[0], "http://localhost:9999/v1")

    def test_model_resolution_prefers_configured_else_capable(self):
        models = ["small-1.5b", "big-70b-instruct", "mid-7b"]
        cfg = self._cfg(extract_local="big-70b-instruct")
        self.assertEqual(L.resolve_local_model(cfg, models), "big-70b-instruct")
        # configured id absent on the server → heuristic falls back to the largest by name
        cfg = self._cfg(extract_local="not-loaded")
        self.assertEqual(L.resolve_local_model(cfg, models), "big-70b-instruct")
        # nothing configured → still the most capable available
        self.assertEqual(L.resolve_local_model(self._cfg(), models), "big-70b-instruct")
        # empty server → None (caller degrades, never crashes)
        self.assertIsNone(L.resolve_local_model(self._cfg(), []))

    def test_build_backend_points_at_live_server_and_model(self):
        self._patch({"http://localhost:11434/v1": ["llama3:8b"]})
        b = L.build_backend("local", self._cfg(local={}))
        self.assertEqual(b.base_url, "http://localhost:11434/v1")
        self.assertEqual(b.model, "llama3:8b")

    def test_none_found_returns_right_fallback_without_raising(self):
        self._patch({})  # no server anywhere
        cfg = self._cfg(local={})
        self.assertFalse(L.local_reachable(cfg))
        self.assertIsNone(L.probe_local(cfg))
        # auto must fall through the chain; with no claude CLI and no key it raises a clear error
        orig_which, orig_env = L.shutil.which, dict(L.os.environ)
        L.shutil.which = lambda *_: None
        L.os.environ.pop("ANTHROPIC_API_KEY", None)
        self.addCleanup(lambda: (setattr(L.shutil, "which", orig_which),
                                 L.os.environ.update(orig_env)))
        with self.assertRaises(RuntimeError):
            L.detect_backend(cfg)
        # build_backend("local") must not raise even with nothing up (graceful default)
        b = L.build_backend("local", cfg)
        self.assertTrue(b.base_url.startswith("http"))


if __name__ == "__main__":
    unittest.main()
