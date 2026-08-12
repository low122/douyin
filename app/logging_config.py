"""Logging setup, applied once per process.

Without this the application's own log records go nowhere: the root logger
defaults to WARNING, so every `log.info` describing what a job is doing is
silently dropped and a failed run leaves nothing to read but the queue's own
one-line summary.
"""

import logging
import sys

from app.config import get_settings

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format=_FORMAT,
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,  # replace whatever uvicorn or arq installed first
    )

    # These are chatty at INFO or DEBUG and say nothing about our own work:
    # httpx logs a line per request, jieba narrates dictionary loading, and the
    # SQL echo is only useful when debugging queries.
    for noisy in ("httpx", "httpcore", "openai", "sqlalchemy.engine", "jieba"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
