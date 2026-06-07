# Phase 1 — Foundation: Voice Loop + KB Agent

> **Goal:** A working voice bot that listens to a question, searches your help docs, and speaks a grounded answer back. One agent, one tool, full voice loop.

---

## Overview

This phase proves the plumbing. By the end you have:
- A microphone → brain → speaker pipeline that works end-to-end
- A knowledge base MCP server serving your real help docs
- A single Resolver agent that calls the KB tool and answers from what it finds
- All running on **Groq cloud inference** (no GPU needed on your laptop)

**No classification, no escalation, no CRM yet.** Just one clean round-trip.

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
| **ChromaDB** | 0.6+ | Vector store for knowledge base | Free (Apache 2.0) |
| **sentence-transformers** | 3.x | Embedding model for docs | Free |
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
│       ├── embedder.py           # embedding + Chroma logic
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

# ── Knowledge Base ─────────────────────────────────────────
KB_COLLECTION_NAME=support_docs
KB_EMBED_MODEL=all-MiniLM-L6-v2
CHROMA_PATH=./chroma_store
KB_MCP_PORT=8000

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

### Step 3 — Build the KB MCP Server

**`mcp_servers/kb_mcp/embedder.py`** — embedding and vector search:

```python
# embedder.py — handles doc ingestion and search
import chromadb
from sentence_transformers import SentenceTransformer
from loguru import logger
import os, glob

CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_store")
COLLECTION  = os.getenv("KB_COLLECTION_NAME", "support_docs")
EMBED_MODEL = os.getenv("KB_EMBED_MODEL", "all-MiniLM-L6-v2")

_client     = chromadb.PersistentClient(path=CHROMA_PATH)
_embedder   = SentenceTransformer(EMBED_MODEL)
_collection = _client.get_or_create_collection(COLLECTION)


def ingest_directory(docs_dir: str, chunk_size: int = 400) -> int:
    """Chunk all .txt and .md files and store embeddings."""
    files  = glob.glob(f"{docs_dir}/**/*.txt", recursive=True)
    files += glob.glob(f"{docs_dir}/**/*.md",  recursive=True)
    total  = 0
    for fp in files:
        text = open(fp).read()
        chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
        embeddings = _embedder.encode(chunks).tolist()
        ids = [f"{fp}-chunk-{i}" for i in range(len(chunks))]
        _collection.add(documents=chunks, embeddings=embeddings, ids=ids)
        total += len(chunks)
        logger.info(f"Ingested {fp} → {len(chunks)} chunks")
    return total


def search(query: str, n_results: int = 3) -> list[str]:
    """Return top-n doc chunks for the query."""
    vec = _embedder.encode([query]).tolist()
    res = _collection.query(query_embeddings=vec, n_results=n_results)
    return res["documents"][0] if res["documents"] else []
```

**`mcp_servers/kb_mcp/server.py`** — FastMCP server exposing the search tool:

```python
# server.py — KB MCP server entry point
from mcp.server.fastmcp import FastMCP
from .embedder import search
from loguru import logger
import os

mcp  = FastMCP("kb-server")
PORT = int(os.getenv("KB_MCP_PORT", 8000))


@mcp.tool()
async def search_kb(query: str) -> str:
    """
    Search the customer support knowledge base.
    Returns relevant document excerpts for the given query.
    Always call this before answering a support question.
    """
    logger.info(f"KB search | query='{query}'")
    chunks = search(query, n_results=3)
    if not chunks:
        return "No relevant information found in the knowledge base."
    return "\n\n---\n\n".join(chunks)


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

if __name__ == "__main__":
    docs_dir = "./mcp_servers/kb_mcp/docs"
    total = ingest_directory(docs_dir)
    print(f"✅ Ingested {total} chunks into ChromaDB")
```

```bash
# run ingestion once (re-run whenever docs change)
python scripts/ingest_docs.py
```

---

### Step 5 — Build the Resolver Agent

**`agent/resolver.py`** — system prompt + tool wiring:

```python
# resolver.py — single Resolver agent definition
RESOLVER_SYSTEM_PROMPT = """
You are a helpful customer support agent for our company.

RULES:
- Always call search_kb FIRST before answering any question.
- Only answer based on what the knowledge base returns.
- If the KB returns nothing useful, say: "I don't have that information.
  Let me connect you with a human agent."
- Keep answers short, clear, and spoken-word friendly.
  No bullet points or markdown — this will be read aloud.
- Be warm but efficient. One sentence max per idea.
"""

# Tool definition passed to Groq function-calling
KB_TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": "search_kb",
        "description": "Search the customer support knowledge base",
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
# tests/test_kb_mcp.py — unit test the KB search
import pytest
from mcp_servers.kb_mcp.embedder import search

def test_search_returns_results():
    results = search("how to reset password")
    assert len(results) > 0
    assert isinstance(results[0], str)

def test_search_empty_for_garbage():
    results = search("zzzzxxx123garbage")
    # may return something but should not crash
    assert isinstance(results, list)
```

```bash
pytest tests/ -v
```

---

## Definition of Done — Phase 1 ✅

- [ ] `docker-compose up` starts Kokoro TTS cleanly
- [ ] KB MCP server starts and responds to search queries
- [ ] Voice pipeline starts without errors
- [ ] Caller speaks → STT transcript visible in logs
- [ ] LLM calls `search_kb` tool for support questions
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
| Pipecat import error | Ensure you installed `pipecat-ai[groq,silero]`, not just `pipecat` |
| Mic not detected | Check system audio permissions; set `AUDIO_DEVICE_INDEX` env var |

---

*Next: Phase 2 — add the Classifier, CRM MCP, Email MCP, confidence gate, and multi-agent handoff.*
