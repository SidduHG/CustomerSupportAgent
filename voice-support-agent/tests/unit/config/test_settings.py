"""Unit tests for the config layer (settings + constants)."""
import pytest

from agent.config.settings import Settings
from agent.config import constants


def test_defaults_applied(monkeypatch):
    # Clear a representative spread of env vars so defaults take effect.
    for key in [
        "GROQ_LLM_MODEL",
        "GROQ_STT_MODEL",
        "KB_MCP_PORT",
        "KB_RERANK_TOP_N",
        "SAMPLE_RATE",
        "LOG_LEVEL",
    ]:
        monkeypatch.delenv(key, raising=False)

    s = Settings.from_env()

    assert s.groq_llm_model == "llama-3.3-70b-versatile"
    assert s.groq_stt_model == "whisper-large-v3-turbo"
    assert s.kb_mcp_port == 8000
    assert s.kb_rerank_top_n == 3
    assert s.sample_rate == 16000
    assert s.log_level == "INFO"


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("GROQ_LLM_MODEL", "custom-model")
    monkeypatch.setenv("KB_MCP_PORT", "9999")

    s = Settings.from_env()

    assert s.groq_llm_model == "custom-model"
    assert s.kb_mcp_port == 9999  # parsed as int, not str


def test_int_parsing_handles_empty(monkeypatch):
    # An empty string should fall back to the default, not raise ValueError.
    monkeypatch.setenv("KB_MCP_PORT", "")
    s = Settings.from_env()
    assert s.kb_mcp_port == 8000


def test_require_groq_key_raises_when_missing(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "")
    s = Settings.from_env()
    with pytest.raises(RuntimeError):
        s.require_groq_key()


def test_require_groq_key_ok_when_set(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key")
    s = Settings.from_env()
    s.require_groq_key()  # should not raise


def test_settings_is_immutable(monkeypatch):
    s = Settings.from_env()
    with pytest.raises(Exception):
        s.kb_mcp_port = 1234  # frozen dataclass


def test_constants_present():
    assert constants.APP_NAME == "voice-support-agent"
    assert constants.GREETING_MESSAGE
    assert constants.FALLBACK_MESSAGE
    assert ".txt" in constants.SUPPORTED_DOC_EXTENSIONS
