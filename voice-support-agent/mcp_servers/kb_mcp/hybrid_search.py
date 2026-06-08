"""Hybrid retrieval: vector + BM25 -> RRF fusion -> cross-encoder rerank.

Combines semantic search (embeddings — good at meaning and paraphrase) with
lexical BM25 (good at the exact terms embeddings blur: error codes, SKUs,
policy numbers), fuses the two ranked lists with Reciprocal Rank Fusion, then
reranks the top candidates with a cross-encoder for final precision. Everything
runs in-process — no extra infrastructure beyond the one rank-bm25 dependency.

The BM25 index lives in memory and is built lazily from whatever is currently
in ChromaDB. After re-ingesting docs, call ``rebuild_bm25_index()`` (or restart
the server) so keyword search sees the new chunks.
"""
from __future__ import annotations

from functools import lru_cache
from threading import Lock

from loguru import logger
from rank_bm25 import BM25Okapi

from agent.config.settings import get_settings

from .embedder import all_chunks, vector_search

RRF_K = 60  # standard Reciprocal Rank Fusion damping constant

_bm25_chunks: list[dict] = []
_bm25_index: BM25Okapi | None = None
_bm25_built = False
_bm25_lock = Lock()


def _tokenize(text: str) -> list[str]:
    """Cheap whitespace tokenizer shared by indexing and querying."""
    return text.lower().split()


def rebuild_bm25_index() -> int:
    """(Re)build the in-memory BM25 index from current ChromaDB contents.

    Returns the number of chunks indexed.
    """
    global _bm25_chunks, _bm25_index, _bm25_built
    with _bm25_lock:
        _bm25_chunks = all_chunks()
        tokenized = [_tokenize(c["text"]) for c in _bm25_chunks]
        _bm25_index = BM25Okapi(tokenized) if tokenized else None
        _bm25_built = True
    logger.info("BM25 index built | {} chunks", len(_bm25_chunks))
    return len(_bm25_chunks)


def _ensure_bm25_index() -> None:
    """Build the index on first use. A ``_bm25_built`` sentinel means a genuinely
    empty corpus isn't re-fetched from ChromaDB on every search."""
    if not _bm25_built:
        rebuild_bm25_index()


def _bm25_search(query: str, top_k: int) -> list[dict]:
    """Lexical search. Returns up to ``top_k`` chunks with a positive BM25
    score (zero-score chunks share no terms with the query and are dropped)."""
    _ensure_bm25_index()
    # Snapshot both globals together under the lock so a concurrent rebuild
    # can't swap the index out from under the chunk list mid-search.
    with _bm25_lock:
        index = _bm25_index
        chunks = _bm25_chunks
    if index is None:
        return []
    scores = index.get_scores(_tokenize(query))
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
    return [
        {
            "text": chunks[i]["text"],
            "metadata": chunks[i]["metadata"],
            "score": float(scores[i]),
        }
        for i in ranked
        if scores[i] > 0
    ]


def _chunk_key(item: dict) -> str:
    """Stable identity for a chunk so the same chunk from both searches fuses
    into one entry. Falls back to text when metadata is absent (e.g. in tests)."""
    meta = item.get("metadata") or {}
    if "doc_name" in meta and "chunk_index" in meta:
        return f"{meta['doc_name']}#{meta['chunk_index']}"
    return item["text"]


def _fuse_rrf(vector_results: list[dict], bm25_results: list[dict]) -> list[dict]:
    """Reciprocal Rank Fusion: a chunk's score is the sum of 1/(RRF_K + rank)
    over each list it appears in, so chunks ranked highly by BOTH methods rise
    to the top while single-list hits still get partial credit."""
    fused: dict[str, dict] = {}
    for results in (vector_results, bm25_results):
        for rank, item in enumerate(results):
            key = _chunk_key(item)
            if key not in fused:
                fused[key] = {**item, "rrf_score": 0.0}
            fused[key]["rrf_score"] += 1.0 / (RRF_K + rank + 1)
    return sorted(fused.values(), key=lambda x: x["rrf_score"], reverse=True)


@lru_cache(maxsize=1)
def _get_reranker():
    from sentence_transformers import CrossEncoder

    settings = get_settings()
    logger.info("Loading reranker model: {}", settings.kb_rerank_model)
    return CrossEncoder(settings.kb_rerank_model)


def _rerank(query: str, candidates: list[dict], top_n: int) -> list[dict]:
    """Cross-encoder scores each (query, chunk) pair directly — far more precise
    than comparing precomputed vectors — and breaks ties between near-duplicates.
    Adds ``rerank_score`` and returns the top ``top_n``."""
    if not candidates:
        return []
    reranker = _get_reranker()
    pairs = [(query, c["text"]) for c in candidates]
    scores = reranker.predict(pairs)
    for c, s in zip(candidates, scores):
        c["rerank_score"] = float(s)
    return sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)[:top_n]


def search(query: str) -> list[dict]:
    """Full hybrid pipeline: vector + BM25 -> RRF fusion -> cross-encoder rerank.

    Returns the final top chunks, each with metadata and a ``rerank_score`` the
    agent can use for grounding, citations, and (in Phase 2) confidence.
    """
    settings = get_settings()
    vec_results = vector_search(query, n_results=settings.kb_vector_top_k)
    bm25_results = _bm25_search(query, top_k=settings.kb_bm25_top_k)
    fused = _fuse_rrf(vec_results, bm25_results)
    # Cap by the SUM, not the max: the fused list can hold up to
    # vector_top_k + bm25_top_k distinct chunks, and the lower-RRF single-list
    # hits are exactly the strong-only matches the cross-encoder exists to
    # rescue — truncating to max() would discard them before reranking.
    candidate_cap = settings.kb_vector_top_k + settings.kb_bm25_top_k
    final = _rerank(query, fused[:candidate_cap], top_n=settings.kb_rerank_top_n)
    logger.info(
        "Hybrid search | query='{}' vector={} bm25={} fused={} final={}",
        query,
        len(vec_results),
        len(bm25_results),
        len(fused),
        len(final),
    )
    return final
