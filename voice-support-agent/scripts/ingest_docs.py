"""Chunk + embed the help docs into ChromaDB.

Run this once, and again whenever the docs change:

    python scripts/ingest_docs.py

The BM25 keyword index (F2) is built in-memory from ChromaDB at MCP-server
startup, so restart the KB server after re-ingesting to pick up new chunks.
"""
import sys
from pathlib import Path

# Allow running as a plain script: put the project root on sys.path so the
# `agent` and `mcp_servers` packages import cleanly.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from agent.config.logging_config import configure_logging  # noqa: E402
from agent.config.settings import get_settings  # noqa: E402
from mcp_servers.kb_mcp.embedder import ingest_directory  # noqa: E402

DOCS_DIR = _PROJECT_ROOT / "mcp_servers" / "kb_mcp" / "docs"


def main() -> None:
    configure_logging(get_settings().log_level)
    total = ingest_directory(str(DOCS_DIR))
    print(f"Ingested {total} chunks into ChromaDB from {DOCS_DIR}")


if __name__ == "__main__":
    main()
