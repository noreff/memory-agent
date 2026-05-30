# knowledge/ — your memory lives here (git-ignored)

This directory holds the generated knowledge base: `index.md` plus one markdown note per topic, each
carrying `sources` provenance and a `conflicts` field. It is **personal data and is git-ignored** —
only this README is tracked, so the directory exists on a fresh clone.

Populate it by running a backfill over your history (`engine/backfill.js`), or let the incremental
loop build it: `mem.py refresh` collects atoms, `mem.py merge` consolidates them into notes. See the
repo README for both paths.
