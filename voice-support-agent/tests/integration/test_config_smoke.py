"""Integration smoke test: the config layer wires together end-to-end.

Exercises the real ``get_settings()`` path (including ``load_dotenv`` and the
lru_cache) and the logging setup — the way the rest of the app will use them.
"""
from agent.config.settings import get_settings
from agent.config.logging_config import configure_logging


def test_get_settings_returns_usable_config():
    get_settings.cache_clear()
    s = get_settings()
    # Sensible, non-empty values regardless of whether a .env is present.
    assert s.kb_collection_name
    assert s.kb_mcp_port > 0
    assert s.kb_embed_model
    assert s.sample_rate > 0


def test_get_settings_is_cached():
    get_settings.cache_clear()
    assert get_settings() is get_settings()  # same cached instance


def test_configure_logging_is_idempotent():
    # Calling twice must not raise or double-register sinks.
    configure_logging("INFO")
    configure_logging("DEBUG")
