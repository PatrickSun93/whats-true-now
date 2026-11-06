"""
Optional dense retrieval (local Ollama embeddings). OFF by default.

Turned on by DUPACO_EMBED=ollama (environment or repo-root .env). Optional:
    OLLAMA_EMBED_MODEL (default nomic-embed-text), OLLAMA_BASE_URL (default http://localhost:11434),
    DUPACO_EMBED_WEIGHT (default 2.0): how much the cosine score adds to the BM25 score,
    DUPACO_EMBED_GATE (default 0.75): use embeddings only when BM25 covers less than this share,
    DUPACO_EMBED_CACHE: a file to keep vectors between runs (outside the repo; it holds site content).
Needs numpy (only in this mode). If Ollama or numpy is missing, search stays BM25-only.

How it is used: every passage is embedded once at ingest (identical texts across snapshots share
one vector). At question time the question is embedded and the passages most similar to it join
the BM25 candidates, so a question worded differently from the website can still find the page
(the hybrid idea from Patrick's healthcare-agentic-rag; a weighted sum instead of RRF, because the
rules downstream compare score ratios).
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import llm   # shares the .env reader

_VEC = {}            # passage text -> row in _MATRIX (shared across snapshots)
_ROWS = []
_MATRIX = None
_BROKEN = False


def enabled() -> bool:
    return not _BROKEN and llm._get("DUPACO_EMBED").lower() == "ollama"


def weight() -> float:
    return float(llm._get("DUPACO_EMBED_WEIGHT", "2.0"))


def min_cover() -> float:
    """Embeddings are used only when the best BM25 passage covers less than this share of the
    question's (idf-weighted) words, i.e. when the member's wording isn't the website's."""
    return float(llm._get("DUPACO_EMBED_GATE", "0.75"))


def _embed(texts: list, prefix: str) -> list:
    url = llm._get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/") + "/api/embed"
    body = {"model": llm._get("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
            "input": [prefix + t[:2000] for t in texts], "truncate": True}
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))["embeddings"]


def index(passages: list):
    """Embed passages not seen before. Returns the row of each passage, or None if unavailable."""
    global _MATRIX, _BROKEN
    try:
        import numpy as np
        cache = llm._get("DUPACO_EMBED_CACHE")
        if cache and not _VEC and Path(cache).is_file():
            saved = np.load(cache, allow_pickle=False)
            for t, v in zip(saved["texts"], saved["vecs"]):
                _VEC[str(t)] = len(_ROWS)
                _ROWS.append(v)
        new = list(dict.fromkeys(p.heading + "\n" + p.text for p in passages
                                 if (p.heading + "\n" + p.text) not in _VEC))
        for i in range(0, len(new), 128):
            batch = new[i:i + 128]
            for t, v in zip(batch, _embed(batch, "search_document: ")):
                _VEC[t] = len(_ROWS)
                _ROWS.append(v)
        if new and cache:
            with open(cache, "wb") as f:
                np.savez(f, texts=np.array(list(_VEC)), vecs=np.asarray(_ROWS, dtype=np.float32))
        if new or _MATRIX is None:
            m = np.asarray(_ROWS, dtype=np.float32)
            _MATRIX = m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)
        return np.asarray([_VEC[p.heading + "\n" + p.text] for p in passages])
    except Exception:
        _BROKEN = True          # no Ollama / no numpy: stay BM25-only
        return None


def search(question: str, rows, top: int = 40) -> list:
    """[(passage index, cosine)] for the passages most similar to the question."""
    if rows is None or _MATRIX is None:
        return []
    try:
        import numpy as np
        q = np.asarray(_embed([question], "search_query: ")[0], dtype=np.float32)
        q /= np.linalg.norm(q) + 1e-9
        sims = _MATRIX[rows] @ q
        best = np.argsort(-sims)[:top]
        return [(int(i), float(sims[i])) for i in best]
    except Exception:
        return []
