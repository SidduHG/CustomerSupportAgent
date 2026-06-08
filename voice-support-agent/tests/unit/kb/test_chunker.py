"""Unit tests for structure-aware chunking (no ML deps required)."""
from mcp_servers.kb_mcp.chunker import (
    chunk_document,
    _split_into_sections,
    _chunk_section,
)


def test_chunking_preserves_metadata(tmp_path):
    fp = tmp_path / "sample.md"
    fp.write_text(
        "# Refunds\nRefunds take 5-7 days.\n\n# Shipping\nOrders ship in 2 days.",
        encoding="utf-8",
    )
    records = chunk_document(str(fp))

    assert len(records) >= 2
    headings = {r["metadata"]["section_heading"] for r in records}
    assert {"Refunds", "Shipping"} <= headings
    assert all(r["metadata"]["doc_name"] == "sample.md" for r in records)
    # chunk_index is unique and contiguous from 0
    assert [r["metadata"]["chunk_index"] for r in records] == list(range(len(records)))


def test_plain_txt_is_single_section(tmp_path):
    fp = tmp_path / "note.txt"
    fp.write_text("Just one paragraph with no headings at all.", encoding="utf-8")
    records = chunk_document(str(fp))

    assert len(records) == 1
    # With no heading, the section falls back to the doc name.
    assert records[0]["metadata"]["section_heading"] == "note.txt"


def test_split_into_sections_detects_headings():
    text = "# A\nalpha text.\n## B\nbeta text."
    sections = _split_into_sections(text)
    assert [h for h, _ in sections] == ["A", "B"]


def test_chunk_section_respects_max_words():
    # 10 short sentences; cap at ~6 words per chunk forces multiple chunks.
    section = " ".join(f"Sentence number {i} here." for i in range(10))
    chunks = _chunk_section(section, max_words=6, overlap_sentences=1)
    assert len(chunks) > 1


def test_overlap_repeats_a_sentence_across_chunks():
    section = "One two three four. Five six seven eight. Nine ten eleven twelve."
    source_words = len(section.split())

    # With overlap, the carried-forward sentence is counted twice across chunks,
    # so the total word count exceeds the source.
    with_overlap = _chunk_section(section, max_words=4, overlap_sentences=1)
    assert len(with_overlap) >= 2
    assert sum(len(c.split()) for c in with_overlap) > source_words

    # Without overlap, no sentence repeats, so totals match the source.
    no_overlap = _chunk_section(section, max_words=4, overlap_sentences=0)
    assert sum(len(c.split()) for c in no_overlap) == source_words
