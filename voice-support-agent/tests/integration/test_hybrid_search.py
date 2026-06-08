"""Integration test: the full hybrid pipeline end-to-end.

vector + BM25 -> RRF fusion -> cross-encoder rerank against a real (temp)
ChromaDB. Requires the ML stack; self-skips otherwise. The reranker model
(~80MB) downloads on first run, then is cached.
"""
import importlib.util

import pytest

_HAS_ML_STACK = (
    importlib.util.find_spec("chromadb") is not None
    and importlib.util.find_spec("sentence_transformers") is not None
    and importlib.util.find_spec("rank_bm25") is not None
)

pytestmark = pytest.mark.skipif(
    not _HAS_ML_STACK,
    reason="ML stack (chromadb / sentence-transformers / rank-bm25) not installed",
)


@pytest.fixture()
def kb(tmp_path, monkeypatch):
    """Isolated ChromaDB + rebuilt BM25 index seeded with a few docs."""
    monkeypatch.setenv("CHROMA_PATH", str(tmp_path / "chroma"))
    monkeypatch.setenv("KB_COLLECTION_NAME", "test_hybrid")

    from agent.config.settings import get_settings

    get_settings.cache_clear()
    from mcp_servers.kb_mcp import embedder
    import mcp_servers.kb_mcp.hybrid_search as hs

    embedder._get_client.cache_clear()
    embedder._get_embedder.cache_clear()

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "password.md").write_text(
        "# Reset Password\nClick forgot password to receive a reset link by email. "
        "Error code PW-204 means your new password is too weak.",
        encoding="utf-8",
    )
    (docs / "shipping.md").write_text(
        "# Shipping\nStandard shipping takes three to five business days within the US.",
        encoding="utf-8",
    )
    (docs / "billing.md").write_text(
        "# Refunds\nWe offer a full refund within 30 days to the original payment method.",
        encoding="utf-8",
    )

    embedder.ingest_directory(str(docs))
    hs.rebuild_bm25_index()  # build keyword index from what we just ingested
    yield hs

    get_settings.cache_clear()
    embedder._get_client.cache_clear()
    embedder._get_embedder.cache_clear()
    hs._bm25_chunks = []
    hs._bm25_index = None


def test_paraphrase_query_finds_right_doc(kb):
    # No shared keywords with the doc — relies on the semantic half.
    results = kb.search("I forgot my login credentials, how do I regain access?")
    assert results
    assert results[0]["metadata"]["doc_name"] == "password.md"
    assert "rerank_score" in results[0]


def test_exact_error_code_is_retrieved(kb):
    # Exact token the embeddings would blur — relies on the BM25 half.
    results = kb.search("PW-204")
    assert results
    assert any(r["metadata"]["doc_name"] == "password.md" for r in results)


def test_results_sorted_by_rerank_score(kb):
    results = kb.search("how long does shipping take")
    assert results
    scores = [r["rerank_score"] for r in results]
    assert scores == sorted(scores, reverse=True)
    assert results[0]["metadata"]["doc_name"] == "shipping.md"


def test_respects_rerank_top_n(kb, monkeypatch):
    monkeypatch.setenv("KB_RERANK_TOP_N", "2")
    from agent.config.settings import get_settings

    get_settings.cache_clear()
    results = kb.search("refund policy")
    assert len(results) <= 2


def test_garbage_query_does_not_crash(kb):
    results = kb.search("zzzzxxx123garbage qwertyuiop")
    assert isinstance(results, list)  # may be empty; must not raise
