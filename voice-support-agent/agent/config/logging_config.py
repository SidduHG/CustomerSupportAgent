"""Centralized loguru logging setup.

Call ``configure_logging()`` once at process startup (pipeline, MCP server,
scripts). Everywhere else, just ``from loguru import logger`` and log.
"""
from __future__ import annotations

import sys

from loguru import logger

_configured = False

_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)


def configure_logging(level: str = "INFO") -> None:
    """Install a single stderr sink at ``level``. Idempotent."""
    global _configured
    if _configured:
        return
    logger.remove()
    logger.add(sys.stderr, level=level, format=_FORMAT, enqueue=True)
    _configured = True
    logger.debug("Logging configured at level {}", level)


__all__ = ["logger", "configure_logging"]
