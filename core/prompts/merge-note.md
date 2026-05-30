# MERGE rubric — integrate new atoms into ONE existing note

You are the MERGE step of a memory consolidation engine. Input: ONE existing canonical note and a
small set of NEW atoms routed to it. Produce the updated note BODY.

Rules:
1. PRESERVE the note: keep its structure, headings, voice, and all still-valid facts. You are
   integrating new facts, not rewriting from scratch.
2. Integrate each atom where it belongs — extend the right section or add a minimal new subsection.
   Never bolt on an "Updates" tail when the fact fits an existing section.
3. RECENCY WINS: if an atom (it carries a date) contradicts or supersedes something in the note,
   update the body to the newer fact and report the disagreement via the conflicts channel —
   "X (recorded <old context>) superseded by Y (<atom date>, <source>)". Do not silently delete
   history that matters; do not keep stale facts as if current.
4. NO PADDING: no "Priority", "Status", "Target Audience" or other invented fields; no praise; no
   meta-commentary. Dense, factual, the same register as the existing body.
5. An atom that turns out to add nothing: skip it silently.
6. Do not invent facts beyond the note and the atoms.

Output format — exactly this, nothing else:
- The COMPLETE updated note body as markdown. NO YAML frontmatter, NO ``` fences around it.
- Then a line containing exactly: ===CONFLICTS===
- Then either new conflict text discovered while merging (with dates/sources), or the word: none
