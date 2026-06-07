"""Typed application settings, loaded once from the environment.

All configuration flows through here. Business logic should call
``get_settings()`` rather than reading ``os.environ`` directly — that keeps
defaults in one place and makes config trivially mockable in tests.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv


def _get(key: str, default: str) -> str:
    """Read a string env var, falling back to ``default`` when unset/empty."""
    value = os.getenv(key)
    return value if value not in (None, "") else default


def _get_int(key: str, default: int) -> int:
    """Read an integer env var, falling back to ``default`` when unset/empty.

    Raises a clear RuntimeError on a non-empty, non-numeric value rather than
    leaking a bare ValueError from int().
    """
    value = os.getenv(key)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(
            f"Environment variable {key}={value!r} is not a valid integer."
        ) from exc


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of all Phase 1 configuration."""

    # ── Groq (LLM + STT) ──────────────────────────────────
    groq_api_key: str
    groq_llm_model: str
    groq_stt_model: str

    # ── Kokoro TTS ────────────────────────────────────────
    kokoro_base_url: str
    kokoro_voice: str

    # ── Knowledge base / hybrid search ────────────────────
    kb_collection_name: str
    kb_embed_model: str
    kb_rerank_model: str
    chroma_path: str
    kb_mcp_port: int
    kb_chunk_max_words: int
    kb_chunk_overlap_sentences: int
    kb_vector_top_k: int
    kb_bm25_top_k: int
    kb_rerank_top_n: int

    # ── Audio ─────────────────────────────────────────────
    sample_rate: int

    # ── Logging ───────────────────────────────────────────
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        """Build a Settings instance from the current process environment."""
        return cls(
            groq_api_key=_get("GROQ_API_KEY", ""),
            groq_llm_model=_get("GROQ_LLM_MODEL", "llama-3.3-70b-versatile"),
            groq_stt_model=_get("GROQ_STT_MODEL", "whisper-large-v3-turbo"),
            kokoro_base_url=_get("KOKORO_BASE_URL", "http://localhost:8880"),
            kokoro_voice=_get("KOKORO_VOICE", "af_heart"),
            kb_collection_name=_get("KB_COLLECTION_NAME", "support_docs"),
            kb_embed_model=_get("KB_EMBED_MODEL", "all-MiniLM-L6-v2"),
            kb_rerank_model=_get(
                "KB_RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2"
            ),
            chroma_path=_get("CHROMA_PATH", "./chroma_store"),
            kb_mcp_port=_get_int("KB_MCP_PORT", 8000),
            kb_chunk_max_words=_get_int("KB_CHUNK_MAX_WORDS", 300),
            kb_chunk_overlap_sentences=_get_int("KB_CHUNK_OVERLAP_SENTENCES", 1),
            kb_vector_top_k=_get_int("KB_VECTOR_TOP_K", 10),
            kb_bm25_top_k=_get_int("KB_BM25_TOP_K", 10),
            kb_rerank_top_n=_get_int("KB_RERANK_TOP_N", 3),
            sample_rate=_get_int("SAMPLE_RATE", 16000),
            log_level=_get("LOG_LEVEL", "INFO"),
        )

    def require_groq_key(self) -> None:
        """Raise if the Groq key is missing.

        Called by components that actually need to reach Groq, so importing
        settings (e.g. in tests) never fails just because no key is set.
        """
        if not self.groq_api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Copy .env.example to .env and add your key."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached, process-wide Settings (loads .env on first call)."""
    load_dotenv()
    return Settings.from_env()
