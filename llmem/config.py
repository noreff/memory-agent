"""Central config for ll-memory. Stdlib-only; no external deps."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "raw"
DISTILLED_DIR = ROOT / "distilled"
NOTES_DIR = ROOT / "notes"
KNOWLEDGE_DIR = ROOT / "knowledge"
MANIFEST = ROOT / "manifest.json"

# Local model server (LM Studio, OpenAI-compatible)
LMSTUDIO = "http://localhost:1234/v1"
MAP_MODEL = "qwen/qwen3.6-35b-a3b"        # backfill EXTRACT model: fast MLX MoE.
# IMPORTANT: use PLAIN JSON, NOT response_format json_schema. LM Studio's structured output
# (Outlines) does not forward repetition-penalty to the MLX backend, so strict schema sends
# this model into degenerate repetition loops. We ask for JSON and validate in code instead.
MAP_MAX_TOKENS = 3000
MAP_TEMP = 0.2
MAP_TOP_P = 0.9
EMBED_MODEL = "text-embedding-nomic-embed-text-v1.5"

# Distillation: tool-result blocks longer than this (lines) get capped.
TOOL_RESULT_MAX_LINES = 6
