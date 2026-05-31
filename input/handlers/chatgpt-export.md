# Format hint: ChatGPT data export (`conversations.json`)

For tool-using ingest agents. The official OpenAI export ships a zip containing
`conversations.json` (plus chat.html, user.json, etc. — the JSON is the source of truth).

- **Top level**: one JSON array; each element is a conversation object:
  `{title, create_time, update_time, mapping, current_node, conversation_id (or id), ...}`.
  Timestamps are unix epochs — date = `create_time` → ISO date. Source id = the conversation id.
- **`mapping` is a node tree, not a list**: `{node_id: {id, message, parent, children}}`. Because
  of edits/regenerations the tree branches. To get the conversation the user actually saw, walk
  BACKWARDS from `current_node` via `parent` links, then reverse — that yields the linear thread.
  (Walking all nodes duplicates abandoned branches; usually skip them.)
- **Messages**: `message.author.role` ∈ user/assistant/system/tool. Keep user + assistant. Text is
  in `message.content.parts` (list — join string parts; skip non-string parts like image refs).
  Skip empty parts, `system` boilerplate, and `tool` payloads. Some assistant messages have
  `metadata.is_visually_hidden_from_conversation: true` — skip those.
- **Scale warning**: the file is often 50–500MB. Do NOT read it whole into context — write a
  helper script that streams/loads it once and writes one clean text file per conversation
  (`USER:`/`ASSISTANT:` turns), then chunk normally (~2,800 words, `<<source: ID chunk=N>>`
  headers).
- **Prioritize by recency** when capped: sort conversations by `update_time` descending; extract
  the recent slice fully and breadth-sample the older tail (raw is immutable — the tail can be
  deepened later).
- Old conversations often describe superseded reality. Extraction should still record the facts —
  the merge layer's recency rule and `conflicts` field handle the layering; dates matter, so
  stamp them faithfully.
