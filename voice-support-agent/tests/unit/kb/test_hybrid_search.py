"""Unit tests for hybrid-search fusion and BM25 (no torch / ChromaDB needed).

BM25 (rank-bm25) is pure-Python and light, so we test it by injecting chunks
via a monkeypatched ``all_chunks`` instead of standing up a real vector store.
"""
import mcp_servers.kb_mcp.hybrid_search as hs


def _reset_index():
    hs._bm25_chunks = []
    hs._bm25_index = None


def test_rrf_fusion_prioritizes_overlap():
    vec = [{"text": "A", "metadata": {}}, {"text": "B", "metadata": {}}]
    bm25 = [{"text": "B", "metadata": {}}, {"text": "C", "metadata": {}}]
    fused = hs._fuse_rrf(vec, bm25)
    # "B" is in both lists, so it should rank first after fusion.
    assert fused[0]["text"] == "B"
    # All three distinct chunks survive fusion.
    assert {f["text"] for f in fused} == {"A", "B", "C"}


def test_rrf_dedups_by_metadata_key():
    # Same chunk (same doc_name + chunk_index) returned by both searches.
    chunk_v = {"text": "same", "metadata": {"doc_name": "d.md", "chunk_index": 0}}
    chunk_b = {"text": "same", "metadata": {"doc_name": "d.md", "chunk_index": 0}}
    fused = hs._fuse_rrf([chunk_v], [chunk_b])
    assert len(fused) == 1
    # Credit from both lists accumulates onto the single entry.
    assert fused[0]["rrf_score"] == 2.0 / (hs.RRF_K + 1)


def test_chunk_key_prefers_metadata_then_text():
    assert hs._chunk_key({"text": "t", "metadata": {"doc_name": "a", "chunk_index": 3}}) == "a#3"
    assert hs._chunk_key({"text": "fallback", "metadata": {}}) == "fallback"


# A small but realistic corpus: BM25's IDF is only positive when a term is
# rarer than half the corpus, so a 2-doc corpus is degenerate. Five chunks with
# the target term appearing once gives a clean positive score.
_CORPUS = [
    {"text": "error code PW-204 means the password is too weak", "metadata": {"doc_name": "pw.md", "chunk_index": 0}},
    {"text": "shipping takes three to five business days", "metadata": {"doc_name": "ship.md", "chunk_index": 0}},
    {"text": "refunds are issued within thirty days", "metadata": {"doc_name": "bill.md", "chunk_index": 0}},
    {"text": "track your order from the orders page", "metadata": {"doc_name": "track.md", "chunk_index": 0}},
    {"text": "international orders may incur customs duties", "metadata": {"doc_name": "intl.md", "chunk_index": 0}},
]


def test_bm25_catches_exact_terms(monkeypatch):
    _reset_index()
    monkeypatch.setattr(hs, "all_chunks", lambda: _CORPUS)
    hs.rebuild_bm25_index()

    results = hs._bm25_search("PW-204", top_k=5)
    assert results
    assert results[0]["metadata"]["doc_name"] == "pw.md"


def test_bm25_drops_zero_score_chunks(monkeypatch):
    _reset_index()
    monkeypatch.setattr(hs, "all_chunks", lambda: _CORPUS)
    hs.rebuild_bm25_index()

    # Only the password chunk contains "PW-204"; non-matching chunks score 0
    # and must be dropped rather than padding the results.
    results = hs._bm25_search("PW-204", top_k=5)
    assert len(results) == 1
    assert results[0]["metadata"]["doc_name"] == "pw.md"


def test_bm25_empty_index_returns_empty(monkeypatch):
    _reset_index()
    monkeypatch.setattr(hs, "all_chunks", lambda: [])
    hs.rebuild_bm25_index()
    assert hs._bm25_search("anything", top_k=5) == []
