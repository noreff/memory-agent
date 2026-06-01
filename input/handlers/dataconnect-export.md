# Format hint: DataConnect exports (Vana)

DataConnect (the Vana desktop app) exports each connected platform as one JSON envelope:
`{company, content, itemLabel, itemsExported, name, runID, scope, syncedAt}`. Everything useful is
under `content`, in namespaced keys like `chatgpt.conversations`, `chatgpt.memories`,
`linkedin.profile`, `linkedin.experience`, `github.*` - each usually `{<dataset>: [...], total}`.

- **chatgpt.conversations** → `conversations` is a list; each item has `title`, `create_time`,
  `update_time`, `message_count`, `id`, and a LINEAR `messages` list (no mapping-tree like the
  official export - much simpler). Treat each conversation as one session: source id = `id`,
  date from `create_time`. Render USER:/ASSISTANT: turns, skip empty/system noise.
- **chatgpt.memories** → `memories` is a list of `{content, created_at, id}` - these are ChatGPT's
  own saved memory entries about the user. Extract each as a candidate fact directly (they are
  already atomic); stamp `created_at` as the date.
- **linkedin.\*** (profile, experience, education, skills, connections) and **github.\*** are
  factual records, not conversations - extract durable facts (roles, dates, employers, skills,
  repo ownership) straight from the structures.
- Files can be tens of MB (thousands of conversations). Do NOT read whole files into context -
  write a helper script to split per conversation/dataset, then chunk normally with
  `<<source: ID chunk=N>>` headers. When capped, sort conversations by `update_time` descending
  and prefer the recent slice; the raw file is immutable so the tail can be deepened later.
