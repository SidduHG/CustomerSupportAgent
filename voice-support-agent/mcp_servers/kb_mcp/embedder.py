"""Embedding + ChromaDB storage — the semantic-search half of hybrid retrieval.

Chunks are embedded with a sentence-transformers model and stored (with their
metadata) in a persistent ChromaDB collection. ``vector_search`` runs semantic
lookups; ``all_chunks`` dumps everything (used by the BM25 index in F2).

The Chroma client and embedding model are loaded lazily and cached, so importing
this module is cheap and tests can run without triggering a model download until
they actually need one.
"""
from __future__ import annotations

import glob
from functools import lru_cache

from loguru import logger

from agent.config.settings import get_settings

from .chunker import chunk_document


@lru_cache(maxsize=1)
def _get_client():
    import chromadb

    settings = get_settings()
    logger.info("Opening ChromaDB at {}", settings.chroma_path)
    return chromadb.PersistentClient(path=settings.chroma_path)


@lru_cache(maxsize=1)
def _get_embedder():
    from sentence_transformers import SentenceTransformer

    settings = get_settings()
    logger.info("Loading embedding model: {}", settings.kb_embed_model)
    return SentenceTransformer(settings.kb_embed_model)


def _get_collection():
    settings = get_settings()
    return _get_client().get_or_create_collection(settings.kb_collection_name)


def ingest_directory(docs_dir: str) -> int:
    """Chunk every .txt/.md file under ``docs_dir`` and upsert each chunk's
    text + embedding + metadata into ChromaDB. Returns the chunk count.

    Uses upsert (not add) so re-running ingestion refreshes existing chunks
    instead of erroring on duplicate IDs.
    """
    files = glob.glob(f"{docs_dir}/**/*.txt", recursive=True)
    files += glob.glob(f"{docs_dir}/**/*.md", recursive=True)
    embedder = _get_embedder()
    collection = _get_collection()
    total = 0
    for fp in sorted(files):
        records = chunk_document(fp)
        if not records:
            continue
        texts = [r["text"] for r in records]
        metadatas = [r["metadata"] for r in records]
        embeddings = embedder.encode(texts).tolist()
        ids = [
            f"{r['metadata']['doc_name']}-chunk-{r['metadata']['chunk_index']}"
            for r in records
        ]
        collection.upsert(
            documents=texts, embeddings=embeddings, metadatas=metadatas, ids=ids
        )
        total += len(records)
        logger.info("Ingested {} -> {} chunks", fp, len(records))
    logger.info("Ingestion complete | {} chunks total", total)
    return total


def vector_search(query: str, n_results: int = 10) -> list[dict]:
    """Semantic search: chunks whose embeddings are closest in meaning to the
    query. Each result keeps its text, metadata, and a similarity score.
    """
    embedder = _get_embedder()
    collection = _get_collection()
    vec = embedder.encode([query]).tolist()
    res = collection.query(
        query_embeddings=vec,
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
    )
    if not res["documents"] or not res["documents"][0]:
        return []
    return [
        {"text": doc, "metadata": meta, "score": 1 - dist}  # distance → similarity
        for doc, meta, dist in zip(
            res["documents"][0], res["metadatas"][0], res["distances"][0]
        )
    ]


def all_chunks() -> list[dict]:
    """Return every stored chunk — used to build the in-memory BM25 index (F2)."""
    collection = _get_collection()
    res = collection.get(include=["documents", "metadatas"])
    return [
        {"text": doc, "metadata": meta}
        for doc, meta in zip(res["documents"], res["metadatas"])
    ]
