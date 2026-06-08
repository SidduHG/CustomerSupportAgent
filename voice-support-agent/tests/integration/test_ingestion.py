"""Integration test: chunk → embed → store → semantic search end-to-end.

Requires the ML stack (chromadb + sentence-transformers). Skipped automatically
if those aren't installed, so the suite still runs in a minimal environment.
The embedding model downloads (~80MB) on first run, then is cached.
"""
import importlib.util

import pytest

_HAS_ML_STACK = (
    importlib.util.find_spec("chromadb") is not None
    and importlib.util.find_spec("sentence_transformers") is not None
)

pytestmark = pytest.mark.skipif(
    not _HAS_ML_STACK,
    reason="chromadb / sentence-transformers not installed",
)


@pytest.fixture()
def kb(tmp_path, monkeypatch):
    """A fresh, isolated ChromaDB-backed KB seeded with two tiny docs."""
    # Point Chroma at a throwaway dir so tests never touch the real store.
    monkeypatch.setenv("CHROMA_PATH", str(tmp_path / "chroma"))
    monkeypatch.setenv("KB_COLLECTION_NAME", "test_docs")

    # Settings + embedder/client caches must be cleared so the env above applies.
    from agent.config.settings import get_settings

    get_settings.cache_clear()
    from mcp_servers.kb_mcp import embedder

    embedder._get_client.cache_clear()
    embedder._get_embedder.cache_clear()

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "password.md").write_text(
        "# Reset Password\nClick forgot password to receive a reset link by email.",
        encoding="utf-8",
    )
    (docs / "shipping.md").write_text(
        "# Shipping\nStandard shipping takes three to five business days.",
        encoding="utf-8",
    )

    count = embedder.ingest_directory(str(docs))
    assert count >= 2
    yield embedder

    get_settings.cache_clear()
    embedder._get_client.cache_clear()
    embedder._get_embedder.cache_clear()


def test_vector_search_returns_scored_results(kb):
    results = kb.vector_search("how do I reset my password", n_results=3)
    assert results, "expected at least one result"
    top = results[0]
    assert "text" in top and "metadata" in top and "score" in top
    # The password doc should win for a password query.
    assert top["metadata"]["doc_name"] == "password.md"


def test_results_are_sorted_by_score(kb):
    results = kb.vector_search("shipping time", n_results=5)
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_all_chunks_returns_everything(kb):
    chunks = kb.all_chunks()
    assert len(chunks) >= 2
    assert all("text" in c and "metadata" in c for c in chunks)


def test_search_empty_collection_is_graceful(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_PATH", str(tmp_path / "empty_chroma"))
    monkeypatch.setenv("KB_COLLECTION_NAME", "empty_docs")
    from agent.config.settings import get_settings

    get_settings.cache_clear()
    from mcp_servers.kb_mcp import embedder

    embedder._get_client.cache_clear()
    embedder._get_embedder.cache_clear()
    try:
        results = embedder.vector_search("anything", n_results=3)
        assert results == []  # no crash on an empty store
    finally:
        get_settings.cache_clear()
        embedder._get_client.cache_clear()
        embedder._get_embedder.cache_clear()
