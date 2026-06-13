---
type: project
sources: [7f3a1c8, b2d9f04, e1c6a7b]
confidence: 0.95
updated: 2026-05-28
conflicts: |
  Cache backend changed: Redis :6379 (2026-03, src 7f3a1c8) superseded by
  Dragonfly :6380 (2026-05, src e1c6a7b). Recency wins; Redis kept as history.
---

# Caching layer

- Current: Dragonfly on `localhost:6380`, started with `docker compose up cache`.
- `CACHE_URL` lives in `.env`, read through `config/cache.py`. Eviction `allkeys-lru`, 2GB cap.
- Was Redis 7 on `:6379` until the 2026-05 migration for multi-threaded throughput.
