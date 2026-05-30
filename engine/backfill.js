export const meta = {
  name: 'memory-backfill',
  description: 'BACKFILL: build a deduplicated memory KB from a whole folder of raw history — agents discover each format; raw is never modified',
  whenToUse: 'Point at any folder (chat logs, docs, transcripts). Agents self-discover an extraction methodology, leave raw untouched, and emit canonical memory notes.',
  phases: [
    { title: 'Ingest',     detail: 'one agent discovers the format + methodology, writes clean chunks to derived/ (skipped when chunks are pre-made)' },
    { title: 'Extract',    detail: 'one agent per chunk → writes atoms to derived/atoms/' },
    { title: 'Cluster',    detail: 'one tool-using agent reads all atoms from disk, dedups/clusters → derived/clusters/' },
    { title: 'Synthesize', detail: 'one agent per cluster reads its atoms from disk → canonical note in derived/notes/' },
  ],
}

// ── args (all paths absolute) ───────────────────────────────────────────────
const A = typeof args === 'string' ? JSON.parse(args) : (args || {})
const rawDir     = A.rawDir
const derivedDir = A.derivedDir
const hintsDir   = A.hintsDir || ''
const chunkWords = A.chunkWords || 2800
const maxChunks  = A.maxChunks || 10
const MODEL      = A.model || 'sonnet'   // default to Sonnet (cost); override via args.model
// Pre-made-chunks mode: if set, skip ingest and run extract over chunkDir/c0001.txt..cNNNN.txt
// (deterministic — no chunk list routed through an agent's structured output, so nothing can be
// silently sampled/truncated). Each chunk file carries its own '<<source: ID ...>>' header.
const chunkDir   = A.chunkDir || ''
const chunkCount = A.chunkCount || 0
if (!rawDir || !derivedDir) throw new Error('args needs { rawDir, derivedDir }; got: ' + JSON.stringify(args))

// ── schemas — metadata ONLY; heavy data (atoms, clusters, notes) lives on disk ─
const INGEST_SCHEMA = {
  type: 'object',
  properties: {
    format: { type: 'string' },
    methodology: { type: 'string' },
    skipped: { type: 'string' },
    rawUnchanged: { type: 'boolean' },
    chunks: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' },
          source: { type: 'string' },
          abs: { type: 'string' },
          words: { type: 'integer' },
        },
        required: ['id', 'source', 'abs'],
      },
    },
  },
  required: ['format', 'methodology', 'chunks'],
}
const EXTRACT_SCHEMA = {
  type: 'object',
  properties: { atomsPath: { type: 'string' }, count: { type: 'integer' } },
  required: ['atomsPath', 'count'],
}
const CLUSTER_INDEX_SCHEMA = {
  type: 'object',
  properties: {
    clustersDir: { type: 'string' },
    clusters: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string' },
          label: { type: 'string' },
          type: { type: 'string' },
          size: { type: 'integer' },
        },
        required: ['file', 'label'],
      },
    },
  },
  required: ['clusters'],
}
const NOTE_SCHEMA = {
  type: 'object',
  properties: {
    slug: { type: 'string' },
    title: { type: 'string' },
    type: { type: 'string' },
    notePath: { type: 'string' },
    sources: { type: 'array', items: { type: 'string' } },
    confidence: { type: 'number' },
    links: { type: 'array', items: { type: 'string' } },
    conflicts: { type: 'string' },
  },
  required: ['slug', 'title', 'notePath'],
}

// ── phase 1: ingest (or use pre-made chunks) ────────────────────────────────
let ingest, chunks
if (chunkDir && chunkCount) {
  log(`using ${chunkCount} pre-made chunks from ${chunkDir} (ingest skipped)`)
  chunks = Array.from({ length: chunkCount }, (_, i) => {
    const id = 'c' + String(i + 1).padStart(4, '0')
    return { id, source: '', abs: `${chunkDir}/${id}.txt` }
  })
  ingest = { format: 'pre-chunked', methodology: `${chunkCount} pre-made chunks from ${chunkDir}; ingest skipped`, rawUnchanged: true, chunks }
} else {
  phase('Ingest')
  ingest = await agent(
`You are the INGEST agent of a memory-building system. Turn a folder of RAW source files into clean, processable text chunks for downstream extraction — and DISCOVER the right method yourself.

RAW dir (READ-ONLY — never modify, move, rename, or delete anything in it): ${rawDir}
DERIVED dir (write everything you produce here): ${derivedDir}
${hintsDir ? `FORMAT HINTS (optional scaffolding you MAY consult): ${hintsDir}` : 'No format hints provided — infer structure from samples.'}

Method (you decide the specifics):
1. Inspect the raw files: 'file', 'wc', 'ls -lS', 'head -c'. Do NOT Read huge files whole.
2. Identify the format(s). If a hint doc matches, use it; otherwise infer the structure from samples.
3. Devise an extraction approach that keeps MEANINGFUL signal and drops noise. For conversational logs: the real human/assistant dialogue, decisions, and preferences — minus tool-call payloads, internal reasoning, system reminders, and boilerplate wrappers. You MAY write and run your own helper scripts (jq/node/python/awk), but write scripts and all outputs ONLY under ${derivedDir}.
4. Emit clean text chunks (~${chunkWords} words each, split on natural boundaries like sessions/turns) as .txt files in ${derivedDir}. Start each chunk file with a provenance header line exactly like: '<<source: SESSION_OR_FILE_ID chunk=N>>'.
5. PROTOTYPE CAP: emit at most ${maxChunks} chunks total. Prefer BREADTH — a representative spread of distinct sources, both small and large — over depth. Report what you skipped.

When done, the raw files must be byte-for-byte unchanged. Return the format(s) found, a one-paragraph description of the methodology you used, what you skipped/capped, rawUnchanged=true, and the chunk manifest.`,
    { schema: INGEST_SCHEMA, agentType: 'general-purpose', model: MODEL, label: 'ingest', phase: 'Ingest' }
  )
  chunks = (ingest?.chunks || []).filter(c => c && c.abs)
}
log(`ingest: ${chunks.length} chunks · rawUnchanged=${ingest?.rawUnchanged}`)
if (!chunks.length) return { error: 'no chunks', ingest }

// ── phase 2: extract — atoms per chunk, WRITTEN TO DISK ─────────────────────
phase('Extract')
const atomsDir = `${derivedDir}/atoms`
const perChunk = await parallel(chunks.map(c => () =>
  agent(
`You are a memory-extraction agent. Read the chunk file at ${c.abs} (it begins with a '<<source: ID chunk=N >>' provenance header). Extract atomic memory candidates — each a single self-contained fact a future agent would want recalled about this user, their projects, decisions, preferences, environment, or durable technical findings.

Rules: one fact per atom, <=2 sentences; classify 'type' (concept/claim/decision/entity/reference/task/user/project); list canonical 'entities' (people, tools, systems, projects, concepts), normalized casing; 'evidence' = short quote/paraphrase; set 'source' to the SESSION ID shown in the chunk's '<<source: ID chunk=N>>' header line; 'confidence' 0-1; skip transient chatter, pleasantries, and tool-specific minutiae that isn't durable.

OUTPUT TO DISK (do NOT return atoms inline): create ${atomsDir} if needed (mkdir -p) and write your atoms as a JSON array to ${atomsDir}/${c.id}.json — each element {claim, type, entities, evidence, source, confidence, tags}. Write nothing outside ${derivedDir}. Then return ONLY {atomsPath: "${atomsDir}/${c.id}.json", count: <number of atoms written>}.`,
    { schema: EXTRACT_SCHEMA, agentType: 'general-purpose', model: MODEL, label: `extract:${c.id}`, phase: 'Extract' }
  )
))
const atomCount = perChunk.filter(Boolean).reduce((n, r) => n + (r.count || 0), 0)
log(`extract: ${atomCount} atoms written across ${perChunk.filter(Boolean).length}/${chunks.length} chunks`)
if (!atomCount) return { error: 'no atoms extracted', ingest }

// ── phase 3: cluster — TOOL-USING agent reads all atoms from disk, dedups ────
phase('Cluster')
const clustersDir = `${derivedDir}/clusters`
const clustered = await agent(
`You are the CLUSTER / REDUCE agent — the global-view step that turns thousands of raw atoms into a deduplicated, synchronized knowledge set. The atoms live as JSON files on disk (one array per chunk):

ATOMS DIR (read-only input): ${atomsDir}    (files: <chunk-id>.json, each an array of {claim,type,entities,evidence,source,confidence,tags})
CLUSTERS DIR (your output):  ${clustersDir}

Do this with TOOLS — do NOT ask for the atoms to be pasted in; assemble the context yourself:
1. Survey: 'ls ${atomsDir}', count files/atoms. The set is LARGE — WRITE AND RUN your own helper script (python/node) under ${derivedDir} to load all atoms, normalize entities, and pre-group by entity/topic so you cluster at scale instead of eyeballing thousands.
2. Cluster: group atoms describing the SAME underlying fact/entity/tightly-coupled concept so duplicates and paraphrases ACROSS sessions merge into ONE cluster. Merge AGGRESSIVELY — the same fact recurs across many sessions. Every atom belongs to exactly one cluster (singletons fine). Prefer well-scoped, specific clusters; AVOID giant 'miscellaneous' catch-all buckets — if atoms don't fit a theme, make small specific clusters rather than one junk drawer.
3. Write: 'mkdir -p ${clustersDir}', then one JSON file per cluster named NN-slug.json containing {label, type, related:[labels], atoms:[member atoms verbatim with their source ids]}.
Write only under ${derivedDir}; only read the atoms inputs.

Return ONLY the index (NOT the atoms): {clustersDir: "${clustersDir}", clusters: [{file, label, type, size}]} where file is the absolute path to each cluster JSON.`,
  { schema: CLUSTER_INDEX_SCHEMA, agentType: 'general-purpose', model: MODEL, label: 'cluster', phase: 'Cluster' }
)
const clusters = (clustered?.clusters || []).filter(c => c && c.file)
log(`cluster: ${atomCount} atoms -> ${clusters.length} clusters`)
if (!clusters.length) return { error: 'cluster produced no clusters', ingest, atomCount }

// ── phase 4: synthesize — one canonical note per cluster ────────────────────
phase('Synthesize')
const notesDir = `${derivedDir}/notes`
const notes = await parallel(clusters.map(c => () =>
  agent(
`Read the cluster file at ${c.file} — it has {label, type, related, atoms}. All atoms are about: "${c.label}".

Write ONE canonical memory note that merges them into a single de-duplicated fact. If atoms conflict, keep the most specific / highest-confidence version and record the disagreement in 'conflicts' with provenance (memory reflects what was true when recorded — note recency where it matters).

OUTPUT TO DISK: 'mkdir -p ${notesDir}', then write the note as markdown to ${notesDir}/<slug>.md (slug = kebab-case of the label) with YAML frontmatter (type, sources, confidence, links, conflicts) then a tight markdown body. 'links' = labels from ${JSON.stringify(c.related || [])}. 'sources' = unique provenance ids from the atoms. Write only under ${derivedDir}.

Return ONLY {slug, title, type, notePath, sources, confidence, links, conflicts}.`,
    { schema: NOTE_SCHEMA, agentType: 'general-purpose', model: MODEL, label: `synth:${(c.label || '').slice(0, 24)}`, phase: 'Synthesize' }
  )
))

return {
  format: ingest?.format,
  methodology: ingest?.methodology,
  rawUnchanged: ingest?.rawUnchanged,
  chunkCount: chunks.length,
  atomCount,
  clusterCount: clusters.length,
  notesDir,
  notes: notes.filter(Boolean),
}
