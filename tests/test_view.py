"""Viewer tests: parsing, fuzzy resolution and render smoke over a tiny synthetic KB.
Run: python3 -m unittest discover tests"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config as cfgmod   # noqa: E402
from engine import view as V        # noqa: E402

NOTE = """---
type: reference
sources:
  - aaaa1111-0000-0000-0000-000000000000
confidence: 0.8
links: []
updated: 2026-06-28
conflicts: |
  [2026-06-28] Redis :6379 superseded by Dragonfly :6380.

  [2026-06-20] older entry with no structure.
---

# Caching Layer

**Setup**
- Dragonfly on `localhost:6380`
- `CACHE_URL` in .env
"""


def _kb(d):
    cfg = cfgmod.Config({"paths": {"knowledge": str(d / "kb"), "state": str(d / "state")},
                         "agents": []}, d)
    cfg.knowledge_dir.mkdir(parents=True)
    (cfg.knowledge_dir / "caching-layer.md").write_text(NOTE)
    (cfg.knowledge_dir / "index.md").write_text("# index\n")  # must be skipped
    led = cfg.state_dir / "derived" / "ledgers"
    led.mkdir(parents=True)
    atom = {"id": "x#1", "claim": "Dragonfly serves cache on port 6380.", "type": "reference",
            "evidence": "we moved to dragonfly :6380", "confidence": "high",
            "source": "aaaa1111-0000-0000-0000-000000000000", "date": "2026-06-28",
            "placed_at": "2026-06-29"}
    (led / "caching-layer.atoms.jsonl").write_text(json.dumps(atom) + "\n")
    return cfg


def _run(fn, *a, **kw):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        fn(*a, **kw)
    return out.getvalue()


class TestViewer(unittest.TestCase):
    def setUp(self):
        V.init(plain=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = _kb(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_notes_skips_index_and_parses_frontmatter(self):
        notes = V.load_notes(self.cfg)
        self.assertEqual([n["slug"] for n in notes], ["caching-layer"])
        n = notes[0]
        self.assertEqual(n["type"], "reference")
        self.assertEqual(n["confidence"], 0.8)
        self.assertIn("Redis", n["conflicts"])

    def test_parse_conflicts_entries(self):
        entries = V.parse_conflicts(V.load_notes(self.cfg)[0]["conflicts"])
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0][0], "2026-06-28")
        self.assertEqual(entries[1][0], "2026-06-20")

    def test_resolve_fuzzy(self):
        notes = V.load_notes(self.cfg)
        self.assertEqual(V.resolve(notes, "caching-layer")[0]["slug"], "caching-layer")
        self.assertEqual(V.resolve(notes, "caching")[0]["slug"], "caching-layer")
        self.assertEqual(V.resolve(notes, "layer")[0]["slug"], "caching-layer")
        self.assertEqual(V.resolve(notes, "nope-nothing"), (None, []))

    def test_wrap_ansi_keeps_first_line_indent(self):
        lines = V.wrap_ansi("    lorem ipsum dolor sit amet", 20, "  ")
        self.assertTrue(lines[0].startswith("    lorem"))
        self.assertTrue(all(V.vlen(ln) <= 20 for ln in lines))

    def test_render_smoke_all_commands(self):
        self.assertIn("caching-layer", _run(V.dashboard, self.cfg))
        self.assertIn("Dragonfly", _run(V.show, self.cfg, "caching"))
        self.assertIn("6380", _run(V.why, self.cfg, "caching-layer"))
        self.assertIn("superseded", _run(V.conflicts, self.cfg))
        self.assertIn("caching-layer", _run(V.log, self.cfg))
        self.assertIn("caching-layer", _run(V.find, self.cfg, ["dragonfly"]))
        self.assertIn("caching-layer", _run(V.list_notes, self.cfg))

    def test_plain_output_has_no_ansi(self):
        out = _run(V.show, self.cfg, "caching-layer")
        self.assertNotIn("\033[", out)


if __name__ == "__main__":
    unittest.main()
