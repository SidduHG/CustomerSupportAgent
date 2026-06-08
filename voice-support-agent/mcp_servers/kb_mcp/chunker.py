"""Structure-aware document chunking.

Splits help docs along natural boundaries — markdown headings first, then
sentence groups within a section — instead of arbitrary character counts, and
tags every chunk with traceable metadata (doc name, section heading, chunk
index) so answers can be cited back to their source.

Chunk-size knobs come from ``settings`` by default but can be overridden per
call, which keeps unit tests deterministic without touching the environment.
"""
from __future__ import annotations

import os
import re

from loguru import logger

from agent.config.settings import get_settings

# Markdown headings (levels 1–3) mark section boundaries.
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$", re.MULTILINE)
# Split on sentence-ending punctuation followed by whitespace.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _split_into_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown on headings; returns ``[(heading, section_text), ...]``.

    Plain .txt files with no headings come back as a single ``("", body)``
    section.
    """
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [("", text.strip())]
    sections = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((m.group(2).strip(), text[start:end].strip()))
    return sections


def _chunk_section(
    section_text: str, max_words: int, overlap_sentences: int
) -> list[str]:
    """Break a section into ~``max_words`` chunks on sentence boundaries.

    Carries the last ``overlap_sentences`` sentences forward into the next
    chunk so an answer that straddles a boundary isn't split in half.
    """
    sentences = [s for s in _SENTENCE_RE.split(section_text) if s.strip()]
    chunks: list[str] = []
    current: list[str] = []
    word_count = 0
    for sent in sentences:
        words = len(sent.split())
        if current and word_count + words > max_words:
            chunks.append(" ".join(current))
            current = current[-overlap_sentences:] if overlap_sentences else []
            word_count = sum(len(s.split()) for s in current)
        current.append(sent)
        word_count += words
    if current:
        chunks.append(" ".join(current))
    return chunks


def chunk_document(
    filepath: str,
    max_words: int | None = None,
    overlap_sentences: int | None = None,
) -> list[dict]:
    """Chunk one file into ``[{"text", "metadata"}, ...]`` records.

    Metadata carries the doc name, section heading, and chunk index — the
    thread back to the source that makes citations and parent-lookups possible.
    """
    settings = get_settings()
    if max_words is None:
        max_words = settings.kb_chunk_max_words
    if overlap_sentences is None:
        overlap_sentences = settings.kb_chunk_overlap_sentences

    doc_name = os.path.basename(filepath)
    with open(filepath, encoding="utf-8") as f:
        text = f.read()

    sections = _split_into_sections(text)
    records: list[dict] = []
    chunk_idx = 0
    for heading, section_text in sections:
        if not section_text:
            continue
        for piece in _chunk_section(section_text, max_words, overlap_sentences):
            records.append(
                {
                    "text": piece,
                    "metadata": {
                        "doc_name": doc_name,
                        "section_heading": heading or doc_name,
                        "chunk_index": chunk_idx,
                    },
                }
            )
            chunk_idx += 1

    logger.info(
        "Chunked {} -> {} chunks across {} section(s)",
        doc_name,
        len(records),
        len(sections),
    )
    return records
