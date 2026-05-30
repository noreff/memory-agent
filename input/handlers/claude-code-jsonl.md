# Format hint: Claude Code transcripts (`*.jsonl`)

For tool-using ingest agents (the backfill). The mechanical fallback (`input/chunk.py`) already
implements all of this.

- One JSON record per line. Conversation records have `type: "user" | "assistant"` and a
  `message: {role, content}`; other types (summary, system) are skippable.
- **Skip sidechains**: any record with `isSidechain: true` is a subagent transcript — noise.
  Whole files whose records are all sidechain should be skipped entirely.
- `content` is either a string or a list of blocks. Keep `text` blocks. For `tool_result` blocks,
  keep the FIRST few lines only — durable machine facts (paths, ports, versions, installed
  services) live there; the rest is bulk. Drop `tool_use` payloads and `thinking` blocks.
- **Echo suppression (important)**: strip `<system-reminder>…</system-reminder>` spans,
  `<command-name>/<command-message>/<local-command-stdout>` wrappers, any text containing the
  sentinel `<<memory-agent:internal-task>>`, and anything following the header
  `# Your memory of this user` — all of that is injected memory/machinery, and re-extracting it
  creates a feedback loop.
- Session id = the file stem (a UUID). Session date = first record's `timestamp` (ISO, take the
  date part).
- Sessions can be huge (50MB+). Chunk on message boundaries, ~2,800 words per chunk, each chunk
  prefixed with a provenance header line: `<<source: SESSION_ID chunk=N>>`.
