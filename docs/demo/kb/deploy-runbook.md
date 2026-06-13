---
type: project
sources: [c80a55d, 1f9e7b3]
confidence: 0.92
updated: 2026-05-29
conflicts: ""
---

# Deploy

- `main` auto-deploys to staging; production is a manual `gh workflow run release`.
- Migrations run before the app boots; never squash a migration that has shipped.
