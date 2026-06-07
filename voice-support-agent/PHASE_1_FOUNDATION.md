# Phase 1 — Foundation: Voice Loop + KB Agent

> **Goal:** A working voice bot that listens to a question, searches your help docs, and speaks a grounded answer back. One agent, one tool, full voice loop.

---

## Overview

This phase proves the plumbing. By the end you have:
- A microphone → brain → speaker pipeline that works end-to-end
- A knowledge base MCP server serving your real help docs — powered by **hybrid search** (semantic + keyword + reranking), not just plain vector lookup
- A single Resolver agent that calls the KB tool and answers from what it finds
- All running on **Groq cloud inference** (no GPU needed on your laptop)

**No classification, no escalation, no CRM yet.** Just one clean round-trip — but with a retrieval layer that's already production-grade, so Phase 2 and 3 inherit a strong foundation instead of having to retrofit one later.

> **Why hybrid search from day one?** A single vector search works fine on a handful of docs, but real help centers run into two recurring problems: (1) embeddings blur exact terms — error codes, SKUs, policy numbers — and (2) near-duplicate chunks score similarly, leaving the model to guess. Hybrid search fixes both: a keyword (BM25) index catches exact terms that embeddings miss, and a reranking pass resolves ambiguity between close-scoring candidates. All of this runs in-process — no extra infrastructure, just one extra Python dependency (`rank-bm25`).

---

## Production Stack

| Tool | Version | Role | Cost |
|------|---------|------|------|
| **Pipecat** | v1.0+ | Voice pipeline orchestration | Free (BSD-2) |
| **Groq API** | — | LLM inference + Whisper STT | Free tier → $0.05–0.79/M tokens |
| **Llama 3.3 70B Versatile** | — | Resolver agent brain | Via Groq |
| **Whisper Large v3 Turbo** | — | Speech-to-text | Via Groq ($0.04/hr audio) |
| **Silero VAD** | v5 | Voice activity detection (turn-taking) | Free (bundled with Pipecat) |
| **Kokoro TTS** | v1.0 | Text-to-speech (self-hosted Docker) | Free (Apache 2.0) |
| **FastMCP** | 2.x | KB MCP server framework | Free (Apache 2.0) |
| **ChromaDB** | 0.6+ | Vector store for knowledge base (semantic search half of hybrid retrieval) | Free (Apache 2.0) |
| **sentence-transformers** | 3.x | Embedding model for docs **and** cross-encoder reranker | Free |
| **rank-bm25** | 0.2+ | Lexical/keyword index — the BM25 half of hybrid retrieval (catches exact terms embeddings blur) | Free (Apache 2.0) |
| **Python** | 3.11+ | Runtime | Free |
| **Docker** | 26+ | Container for Kokoro TTS | Free |
| **python-dotenv** | — | Environment variable management | Free |
| **loguru** | — | Structured logging | Free |

---

## API Keys Required

| Key | Where to get | Cost |
|-----|-------------|------|
| `GROQ_API_KEY` | https://console.groq.com — free, no card | Free tier: 30 RPM |

That is the **only** key you need in Phase 1.

---

## Project Structure

```
voice-support-triage/
├── .env                          # all secrets (never commit)
├── .env.example                  # committed template
├── .gitignore
├── requirements.txt
├── docker-compose.yml            # Kokoro TTS container
│
├── scripts/
│   ├── ingest_docs.py            # chunk + embed your help docs
│   └── test_kb.py                # verify KB search works
│
├── mcp_servers/
│   └── kb_mcp/
│       ├── __init__.py
│       ├── server.py             # FastMCP KB server (main entry)
│       ├── chunker.py            # NEW: structure-aware chunking (splits on headings, keeps metadata)
│       ├── embedder.py           # embedding + ChromaDB ingestion/storage
│       ├── hybrid_search.py      # NEW: BM25 + vector search → RRF fusion → cross-encoder rerank
│       └── docs/                 # drop your .txt / .md help docs here
│           ├── reset_password.txt
│           ├── billing_faq.txt
│           └── shipping_policy.txt
│
├── agent/
│   ├── __init__.py
│   ├── config.py                 # settings, model names, thresholds
│   ├── pipeline.py               # Pipecat pipeline assembly (main entry)
│   ├── resolver.py               # Resolver agent logic + system prompt
│   └── tools.py                  # MCP client that calls KB tool
│
└── tests/
    ├── test_kb_mcp.py
    └── test_pipeline.py
```

---

## Environment Variables

Create `.env` from this template:

```bash
# .env — Phase 1

# ── LLM + STT (Groq) ───────────────────────────────────────
GROQ_API_KEY=gsk_your_key_here
GROQ_LLM_MODEL=llama-3.3-70b-versatile
GROQ_STT_MODEL=whisper-large-v3-turbo

# ── TTS (Kokoro — local Docker) ────────────────────────────
KOKORO_BASE_URL=http://localhost:8880
KOKORO_VOICE=af_heart

# ── Knowledge Base (Hybrid Search) ─────────────────────────
KB_COLLECTION_NAME=support_docs
KB_EMBED_MODEL=all-MiniLM-L6-v2
KB_RERANK_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
CHROMA_PATH=./chroma_store
KB_MCP_PORT=8000
KB_CHUNK_MAX_WORDS=300
KB_CHUNK_OVERLAP_SENTENCES=1
KB_VECTOR_TOP_K=10
KB_BM25_TOP_K=10
KB_RERANK_TOP_N=3

# ── Audio ──────────────────────────────────────────────────
SAMPLE_RATE=16000
```

---

## Step-by-Step Implementation

### Step 1 — Project Setup

```bash
# create project + virtual environment
mkdir voice-support-triage && cd voice-support-triage
python3.11 -m venv .venv && source .venv/bin/activate

# install all dependencies
pip install \
  pipecat-ai[groq,silero] \
  groq \
  mcp \
  chromadb \
  sentence-transformers \
  rank-bm25 \
  python-dotenv \
  loguru \
  aiohttp \
  httpx

# set up git + secrets
cp .env.example .env
echo ".env" >> .gitignore
echo ".venv/" >> .gitignore
echo "chroma_store/" >> .gitignore
```

---

### Step 2 — Spin Up Kokoro TTS (Docker)

Kokoro runs as a self-hosted server that exposes an OpenAI-compatible `/v1/audio/speech` endpoint.

```yaml
# docker-compose.yml
version: "3.9"
services:
  kokoro-tts:
    image: ghcr.io/remsky/kokoro-fastapi-cpu:v0.2.2
    container_name: kokoro_tts
    ports:
      - "8880:8880"
    restart: unless-stopped
    environment:
      - PYTHONUNBUFFERED=1
```

```bash
# start TTS server
docker-compose up -d kokoro-tts

# verify it's running
curl http://localhost:8880/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"kokoro","input":"Hello, voice triage system online.","voice":"af_heart"}' \
  --output test.mp3
```

---

### Step 3 — Build the KB MCP Server (Hybrid Search)

This server is built in three layers: **chunking** (split docs into coherent, metadata-tagged units), **storage** (embed + index those units for both semantic and keyword search), and **hybrid retrieval** (run both searches, fuse the results, and rerank the top candidates). This is more than a "minimum viable" KB — but every piece is a thin, in-process Python module. No new infrastructure.

**`mcp_servers/kb_mcp/chunker.py`** — structure-aware chunking with metadata:

```python
# chunker.py — splits docs along natural boundaries (headings/paragraphs),
# not arbitrary character counts, and tags each chunk with traceable metadata
import re, os
from loguru import logger

MAX_WORDS = int(os.getenv("KB_CHUNK_MAX_WORDS", 300))
OVERLAP_SENTENCES = int(os.getenv("KB_CHUNK_OVERLAP_SENTENCES", 1))

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$", re.MULTILINE)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _split_into_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown on headings; returns [(heading, section_text), ...].
    Plain .txt files (no headings) come back as a single section."""
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [("", text.strip())]
    sections = []
    for i, m in enumerate(matches):
        start = m.end()
        end   = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((m.group(2).strip(), text[start:end].strip()))
    return sections


def _chunk_section(section_text: str) -> list[str]:
    """Break a long section into ~MAX_WORDS chunks on sentence boundaries,
    carrying the last OVERLAP_SENTENCES sentences forward so an answer
    that straddles a chunk boundary doesn't get lost."""
    sentences = _SENTENCE_RE.split(section_text)
    chunks, current, word_count = [], [], 0
    for sent in sentences:
        words = len(sent.split())
        if current and word_count + words > MAX_WORDS:
            chunks.append(" ".join(current))
            current = current[-OVERLAP_SENTENCES:] if OVERLAP_SENTENCES else []
            word_count = sum(len(s.split()) for s in current)
        current.append(sent)
        word_count += words
    if current:
        chunks.append(" ".join(current))
    return chunks


def chunk_document(filepath: str) -> list[dict]:
    """Returns a list of {text, metadata} dicts ready for embedding + indexing.
    Metadata carries the doc name, section heading, and chunk index — the
    'thread back to the source' that makes citations and parent-lookups possible."""
    doc_name = os.path.basename(filepath)
    text     = open(filepath, encoding="utf-8").read()
    records  = []
    chunk_idx = 0
    for heading, section_text in _split_into_sections(text):
        if not section_text:
            continue
        for piece in _chunk_section(section_text):
            records.append({
                "text": piece,
                "metadata": {
                    "doc_name":        doc_name,
                    "section_heading": heading or doc_name,
                    "chunk_index":     chunk_idx,
                },
            })
            chunk_idx += 1
    logger.info(f"Chunked {doc_name} → {len(records)} chunks across "
                f"{len(_split_into_sections(text))} section(s)")
    return records
```

**`mcp_servers/kb_mcp/embedder.py`** — embedding + ChromaDB storage (the semantic-search half):

```python
# embedder.py — embeds chunks and stores them (with metadata) in ChromaDB
import chromadb
from sentence_transformers import SentenceTransformer
from loguru import logger
import os, glob

from .chunker import chunk_document

CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_store")
COLLECTION  = os.getenv("KB_COLLECTION_NAME", "support_docs")
EMBED_MODEL = os.getenv("KB_EMBED_MODEL", "all-MiniLM-L6-v2")

_client     = chromadb.PersistentClient(path=CHROMA_PATH)
_embedder   = SentenceTransformer(EMBED_MODEL)
_collection = _client.get_or_create_collection(COLLECTION)


def ingest_directory(docs_dir: str) -> int:
    """Chunk every .txt/.md file (structure-aware, with metadata) and
    store each chunk's text + embedding + metadata in ChromaDB."""
    files  = glob.glob(f"{docs_dir}/**/*.txt", recursive=True)
    files += glob.glob(f"{docs_dir}/**/*.md",  recursive=True)
    total  = 0
    for fp in files:
        records = chunk_document(fp)
        if not records:
            continue
        texts      = [r["text"] for r in records]
        metadatas  = [r["metadata"] for r in records]
        embeddings = _embedder.encode(texts).tolist()
        ids        = [f"{r['metadata']['doc_name']}-chunk-{r['metadata']['chunk_index']}"
                      for r in records]
        _collection.add(documents=texts, embeddings=embeddings,
                        metadatas=metadatas, ids=ids)
        total += len(records)
        logger.info(f"Ingested {fp} → {len(records)} chunks (with metadata)")
    return total


def vector_search(query: str, n_results: int = 10) -> list[dict]:
    """Semantic search: returns chunks whose embeddings are closest in
    meaning to the query. Each result keeps its text, metadata, and score."""
    vec = _embedder.encode([query]).tolist()
    res = _collection.query(
        query_embeddings=vec, n_results=n_results,
        include=["documents", "metadatas", "distances"],
    )
    if not res["documents"] or not res["documents"][0]:
        return []
    return [
        {"text": doc, "metadata": meta, "score": 1 - dist}  # convert distance → similarity
        for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0])
    ]


def all_chunks() -> list[dict]:
    """Returns every stored chunk — used to build the in-memory BM25 index."""
    res = _collection.get(include=["documents", "metadatas"])
    return [{"text": doc, "metadata": meta}
            for doc, meta in zip(res["documents"], res["metadatas"])]
```

**`mcp_servers/kb_mcp/hybrid_search.py`** — the retrieval brain: BM25 + vector search → fusion → rerank:

```python
# hybrid_search.py — combines semantic (vector) and lexical (BM25) search,
# fuses the two ranked lists with Reciprocal Rank Fusion, then runs a
# cross-encoder reranker to resolve close-scoring ambiguity before
# handing the final, best-matching chunks back to the agent.
import os
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder
from loguru import logger

from .embedder import vector_search, all_chunks

VECTOR_TOP_K  = int(os.getenv("KB_VECTOR_TOP_K", 10))
BM25_TOP_K    = int(os.getenv("KB_BM25_TOP_K", 10))
RERANK_TOP_N  = int(os.getenv("KB_RERANK_TOP_N", 3))
RERANK_MODEL  = os.getenv("KB_RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
RRF_K         = 60  # standard RRF damping constant

_reranker = CrossEncoder(RERANK_MODEL)

# BM25 index is built once at startup from whatever's already in ChromaDB.
# Rebuild it (call _build_bm25_index()) after re-running ingestion.
_bm25_chunks = []
_bm25_index  = None


def _build_bm25_index():
    global _bm25_chunks, _bm25_index
    _bm25_chunks = all_chunks()
    tokenized    = [c["text"].lower().split() for c in _bm25_chunks]
    _bm25_index  = BM25Okapi(tokenized) if tokenized else None
    logger.info(f"BM25 index built | {len(_bm25_chunks)} chunks")


def _bm25_search(query: str, top_k: int) -> list[dict]:
    if _bm25_index is None:
        _build_bm25_index()
    if _bm25_index is None:
        return []
    scores = _bm25_index.get_scores(query.lower().split())
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
    return [{"text": _bm25_chunks[i]["text"], "metadata": _bm25_chunks[i]["metadata"],
             "score": float(scores[i])} for i in ranked]


def _fuse_rrf(vector_results: list[dict], bm25_results: list[dict]) -> list[dict]:
    """Reciprocal Rank Fusion: chunks ranked highly by BOTH methods rise
    to the top; chunks only one method liked still get partial credit."""
    fused: dict[str, dict] = {}
    for rank, results in enumerate([vector_results, bm25_results]):
        for i, item in enumerate(results):
            key = item["text"]
            if key not in fused:
                fused[key] = {**item, "rrf_score": 0.0}
            fused[key]["rrf_score"] += 1.0 / (RRF_K + i + 1)
    return sorted(fused.values(), key=lambda x: x["rrf_score"], reverse=True)


def _rerank(query: str, candidates: list[dict], top_n: int) -> list[dict]:
    """Cross-encoder scores (query, chunk) pairs directly — far more precise
    than comparing pre-computed vectors — and breaks ties between
    near-duplicate matches."""
    if not candidates:
        return []
    pairs  = [(query, c["text"]) for c in candidates]
    scores = _reranker.predict(pairs)
    for c, s in zip(candidates, scores):
        c["rerank_score"] = float(s)
    return sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)[:top_n]


def search(query: str) -> list[dict]:
    """Full hybrid pipeline: vector search + BM25 search → RRF fusion →
    cross-encoder rerank. Returns the final top chunks with metadata
    and a relevance score the agent can use for citations and confidence."""
    vec_results  = vector_search(query, n_results=VECTOR_TOP_K)
    bm25_results = _bm25_search(query, top_k=BM25_TOP_K)
    fused        = _fuse_rrf(vec_results, bm25_results)
    final        = _rerank(query, fused[:max(VECTOR_TOP_K, BM25_TOP_K)], top_n=RERANK_TOP_N)
    logger.info(f"Hybrid search | query='{query}' vector={len(vec_results)} "
                f"bm25={len(bm25_results)} fused={len(fused)} final={len(final)}")
    return final
```

**`mcp_servers/kb_mcp/server.py`** — FastMCP server exposing the hybrid search tool:

```python
# server.py — KB MCP server entry point
from mcp.server.fastmcp import FastMCP
from .hybrid_search import search
from loguru import logger
import os

mcp  = FastMCP("kb-server")
PORT = int(os.getenv("KB_MCP_PORT", 8000))


@mcp.tool()
async def search_kb(query: str) -> str:
    """
    Search the customer support knowledge base using hybrid retrieval
    (semantic + keyword search, fused and reranked for relevance).
    Returns the most relevant document excerpts, each labeled with its
    source document and section so answers can be grounded and cited.
    Always call this before answering a support question.
    """
    logger.info(f"KB search | query='{query}'")
    results = search(query)
    if not results:
        return "No relevant information found in the knowledge base."
    return "\n\n---\n\n".join(
        f"[Source: {r['metadata']['doc_name']} — {r['metadata']['section_heading']} "
        f"| relevance: {r['rerank_score']:.2f}]\n{r['text']}"
        for r in results
    )


if __name__ == "__main__":
    mcp.run(transport="sse", port=PORT)
```

---

### Step 4 — Ingest Your Help Docs

```python
# scripts/ingest_docs.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from mcp_servers.kb_mcp.embedder import ingest_directory
from mcp_servers.kb_mcp.hybrid_search import _build_bm25_index

if __name__ == "__main__":
    docs_dir = "./mcp_servers/kb_mcp/docs"
    total = ingest_directory(docs_dir)
    _build_bm25_index()   # rebuild the lexical index so BM25 sees the new chunks too
    print(f"✅ Ingested {total} chunks into ChromaDB and rebuilt the BM25 index")
```

```bash
# run ingestion once (re-run whenever docs change — this refreshes
# BOTH the vector store and the BM25 keyword index)
python scripts/ingest_docs.py
```

> **Note:** because the BM25 index is built in-memory from whatever's in ChromaDB, the KB MCP server should be restarted after ingestion (or call `_build_bm25_index()` again) so it picks up the latest chunks for keyword search too.

---

### Step 5 — Build the Resolver Agent

**`agent/resolver.py`** — system prompt + tool wiring:

```python
# resolver.py — single Resolver agent definition
RESOLVER_SYSTEM_PROMPT = """
You are a helpful customer support agent for our company.

RULES:
- Always call search_kb FIRST before answering any question.
- Only answer based on what the knowledge base returns — each result is
  labeled with its source document, section, and a relevance score; trust
  higher-relevance excerpts over lower ones if they conflict.
- If the KB returns nothing useful (or relevance scores are low), say:
  "I don't have that information. Let me connect you with a human agent."
- Keep answers short, clear, and spoken-word friendly.
  No bullet points or markdown — this will be read aloud.
  Don't read source labels or scores aloud — they're for your grounding only.
- Be warm but efficient. One sentence max per idea.
"""

# Tool definition passed to Groq function-calling
KB_TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": "search_kb",
        "description": ("Search the customer support knowledge base using hybrid "
                        "retrieval (semantic + keyword search, reranked for relevance). "
                        "Returns the best-matching excerpts with source citations."),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The customer's question or search terms"
                }
            },
            "required": ["query"]
        }
    }
}
```

---

### Step 6 — Assemble the Pipecat Pipeline

**`agent/pipeline.py`** — the main entry point:

```python
# pipeline.py — Pipecat voice pipeline
import asyncio, os
from dotenv import load_dotenv
load_dotenv()

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.processors.aggregators.openai_llm_context import (
    OpenAILLMContext,
    OpenAILLMContextAggregator,
)
from pipecat.services.groq import GroqSTTService
from pipecat.services.openai import OpenAILLMService   # Groq is OAI-compatible
from pipecat.services.kokoro import KokoroTTSService
from pipecat.vad.silero import SileroVADAnalyzer
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioParams

from agent.resolver import RESOLVER_SYSTEM_PROMPT, KB_TOOL_SPEC
from agent.tools import handle_tool_call


async def run_pipeline():
    # ── Transport (microphone + speaker) ──────────────────────
    transport = LocalAudioTransport(
        LocalAudioParams(audio_in_enabled=True, audio_out_enabled=True)
    )

    # ── STT: Groq Whisper ───────────────────────────────────────
    stt = GroqSTTService(
        api_key=os.environ["GROQ_API_KEY"],
        model=os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo"),
    )

    # ── LLM: Groq Llama (OpenAI-compatible endpoint) ───────────
    llm = OpenAILLMService(
        api_key=os.environ["GROQ_API_KEY"],
        base_url="https://api.groq.com/openai/v1",
        model=os.getenv("GROQ_LLM_MODEL", "llama-3.3-70b-versatile"),
    )

    # ── TTS: Kokoro (local Docker) ──────────────────────────────
    tts = KokoroTTSService(
        base_url=os.getenv("KOKORO_BASE_URL", "http://localhost:8880"),
        voice=os.getenv("KOKORO_VOICE", "af_heart"),
    )

    # ── LLM context: system prompt + KB tool ───────────────────
    context = OpenAILLMContext(
        messages=[{"role": "system", "content": RESOLVER_SYSTEM_PROMPT}],
        tools=[KB_TOOL_SPEC],
    )
    context_aggregator = llm.create_context_aggregator(context)

    # ── Pipeline: VAD → STT → LLM → TTS ───────────────────────
    pipeline = Pipeline([
        transport.input(),          # mic audio in
        stt,                        # speech → text
        context_aggregator.user(),  # accumulate user turn
        llm,                        # text → response + tool calls
        tts,                        # text → audio
        transport.output(),         # speaker audio out
        context_aggregator.assistant(),
    ])

    # ── Event hooks ────────────────────────────────────────────
    @transport.event_handler("on_client_connected")
    async def on_connect(transport, client):
        await transport.send_message(
            "Hello! I am your support assistant. How can I help you today?"
        )

    # ── Run ────────────────────────────────────────────────────
    runner = PipelineRunner()
    task   = PipelineTask(pipeline, PipelineParams(allow_interruptions=True))
    await runner.run(task)


if __name__ == "__main__":
    asyncio.run(run_pipeline())
```

**`agent/tools.py`** — routes LLM tool calls to the MCP server:

```python
# tools.py — MCP client that calls KB search
import httpx, json, os
from loguru import logger

KB_MCP_URL = f"http://localhost:{os.getenv('KB_MCP_PORT', 8000)}"


async def handle_tool_call(tool_name: str, tool_args: dict) -> str:
    """Called by Pipecat when the LLM emits a function call."""
    logger.info(f"Tool call | name={tool_name} args={tool_args}")
    if tool_name == "search_kb":
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{KB_MCP_URL}/tools/search_kb",
                json={"query": tool_args.get("query", "")},
                timeout=10.0,
            )
        result = resp.json().get("result", "No results found.")
        logger.info(f"KB result | length={len(result)} chars")
        return result
    return f"Unknown tool: {tool_name}"
```

---

### Step 7 — Run and Test

```bash
# terminal 1: start Kokoro TTS
docker-compose up kokoro-tts

# terminal 2: start KB MCP server
python -m mcp_servers.kb_mcp.server

# terminal 3: run the voice pipeline
python agent/pipeline.py
```

**Test manually:** Ask out loud:
- *"How do I reset my password?"* → should retrieve from KB and answer
- *"What is your refund policy?"* → should search and respond
- *"Tell me a joke"* → should say it has no information

---

## Testing / Validation

```python
# tests/test_kb_mcp.py — unit test each layer of hybrid search
import pytest
from mcp_servers.kb_mcp.chunker import chunk_document
from mcp_servers.kb_mcp.embedder import vector_search
from mcp_servers.kb_mcp.hybrid_search import search, _bm25_search, _fuse_rrf, _build_bm25_index

def test_chunking_preserves_metadata(tmp_path):
    fp = tmp_path / "sample.md"
    fp.write_text("# Refunds\nRefunds take 5-7 days.\n\n# Shipping\nOrders ship in 2 days.")
    records = chunk_document(str(fp))
    assert len(records) >= 2
    assert records[0]["metadata"]["section_heading"] in ("Refunds", "Shipping")
    assert "doc_name" in records[0]["metadata"]

def test_vector_search_returns_scored_results():
    results = vector_search("how to reset password")
    assert isinstance(results, list)
    if results:
        assert "score" in results[0] and "metadata" in results[0]

def test_bm25_catches_exact_terms():
    _build_bm25_index()
    results = _bm25_search("refund", top_k=5)
    assert isinstance(results, list)

def test_rrf_fusion_prioritizes_overlap():
    vec  = [{"text": "A", "metadata": {}}, {"text": "B", "metadata": {}}]
    bm25 = [{"text": "B", "metadata": {}}, {"text": "C", "metadata": {}}]
    fused = _fuse_rrf(vec, bm25)
    # "B" appears in both lists, so it should rank first after fusion
    assert fused[0]["text"] == "B"

def test_hybrid_search_end_to_end():
    results = search("how do I reset my password")
    assert isinstance(results, list)
    if results:
        assert "rerank_score" in results[0]
        assert results == sorted(results, key=lambda r: r["rerank_score"], reverse=True)

def test_search_empty_for_garbage():
    results = search("zzzzxxx123garbage")
    assert isinstance(results, list)  # may return low-relevance results, but should not crash
```

```bash
pytest tests/ -v
```

---

## Definition of Done — Phase 1 ✅

- [ ] `docker-compose up` starts Kokoro TTS cleanly
- [ ] `python scripts/ingest_docs.py` chunks docs by heading/section, stores metadata in ChromaDB, and builds the BM25 index without errors
- [ ] KB MCP server starts and `search_kb` returns hybrid results — each excerpt labeled with its source document, section, and relevance score
- [ ] Hybrid search sanity check: a query using exact terms from a doc (e.g. an error code) AND a paraphrased version of the same question both return the right chunk
- [ ] Reranking visibly resolves ambiguity — when two chunks both mention the same topic, the higher-relevance one is ranked first
- [ ] Voice pipeline starts without errors
- [ ] Caller speaks → STT transcript visible in logs
- [ ] LLM calls `search_kb` tool for support questions and grounds its answer in the returned excerpts (without reading source labels/scores aloud)
- [ ] Kokoro speaks the answer back within 3 seconds
- [ ] Asking an out-of-scope question gets a graceful "I don't know" reply
- [ ] All secrets are in `.env`, never in code

---

## Common Issues

| Problem | Fix |
|---------|-----|
| Kokoro TTS container not starting | Check Docker is running; try `docker logs kokoro_tts` |
| Groq API rate limit hit | You're on the free tier (30 RPM); add `asyncio.sleep(2)` between test calls |
| ChromaDB "collection not found" | Run `python scripts/ingest_docs.py` first |
| BM25 returns nothing / stale results | The BM25 index is built in-memory — restart the KB server (or call `_build_bm25_index()`) after re-ingesting docs |
| Reranker download is slow on first run | `cross-encoder/ms-marco-MiniLM-L-6-v2` downloads from Hugging Face on first use (~80MB); subsequent runs are cached locally |
| Hybrid search feels slow | Lower `KB_VECTOR_TOP_K`/`KB_BM25_TOP_K` (fewer candidates to fuse and rerank) — reranking is the most expensive step |
| Pipecat import error | Ensure you installed `pipecat-ai[groq,silero]`, not just `pipecat` |
| Mic not detected | Check system audio permissions; set `AUDIO_DEVICE_INDEX` env var |

---

*Next: Phase 2 — add the Classifier, CRM MCP, Email MCP, confidence gate, and multi-agent handoff. The Resolver will inherit `search_kb`'s hybrid results — including per-excerpt relevance scores — which Phase 2 uses to feed the confidence gate's `answer_conf`.*
