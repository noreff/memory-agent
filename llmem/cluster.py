"""Cluster all notes by embedding similarity (mechanical — no model judgment), so the
REDUCE step can merge one small, coherent group at a time instead of the whole corpus."""
from __future__ import annotations
import json
import math
import urllib.request
from pathlib import Path

from config import EMBED_MODEL, KNOWLEDGE_DIR, LMSTUDIO, NOTES_DIR

SIM_THRESHOLD = 0.78   # cosine; greedy single-link. NOTE: still naive — chains via
# centroid update and over-merges. For scale, cluster by project first, then by topic,
# or use a real algorithm. Below the REDUCE budget (~hundreds of notes) the strong model
# can just read all notes and skip clustering entirely.


def embed(texts: list[str]) -> list[list[float]]:
    req = urllib.request.Request(f"{LMSTUDIO}/embeddings",
        data=json.dumps({"model": EMBED_MODEL, "input": texts}).encode(),
        headers={"Content-Type": "application/json"})
    data = json.loads(urllib.request.urlopen(req, timeout=300).read())
    return [d["embedding"] for d in data["data"]]


def cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb + 1e-9)


def load_notes() -> list[dict]:
    notes = []
    for f in sorted(NOTES_DIR.glob("*.json")):
        notes.extend(json.loads(f.read_text(encoding="utf-8")))
    return notes


def cluster(notes: list[dict]) -> list[list[dict]]:
    if not notes:
        return []
    vecs = embed([f"{n['topic']}: {n['claim']}" for n in notes])
    clusters, centroids = [], []
    for n, v in zip(notes, vecs):
        best, bi = 0.0, -1
        for i, c in enumerate(centroids):
            s = cos(v, c)
            if s > best:
                best, bi = s, i
        if best >= SIM_THRESHOLD:
            clusters[bi].append(n)
            k = len(clusters[bi])
            centroids[bi] = [(c * (k - 1) + x) / k for c, x in zip(centroids[bi], v)]
        else:
            clusters.append([n]); centroids.append(list(v))
    return sorted(clusters, key=len, reverse=True)


def main():
    notes = load_notes()
    clusters = cluster(notes)
    KNOWLEDGE_DIR.mkdir(exist_ok=True)
    out = [{"topic_hint": c[0]["topic"], "size": len(c), "notes": c} for c in clusters]
    (KNOWLEDGE_DIR / "_clusters.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{len(notes)} notes -> {len(clusters)} clusters")
    for c in out:
        print(f"  [{c['size']}] {c['topic_hint']}")


if __name__ == "__main__":
    main()
