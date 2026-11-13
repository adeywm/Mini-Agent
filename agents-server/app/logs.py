"""Backend logging: readable lines with time, level and source, and secrets masked everywhere.

Get loggers with ``get_logger``: each one carries ``SecretFilter``, which redacts the message,
its arguments and any traceback (tokens, keys, private keys) before a handler sees the record,
so detailed error messages are safe to log. Level: ``MINI_AGENT_LOG_LEVEL`` (default INFO).
"""

from __future__ import annotations

import logging
import traceback

from app.security.redaction import redact_text

FORMAT = "%(asctime)s %(levelname)-7s %(name)-18s %(message)s"


class SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage())
        record.args = None
        if record.exc_info and record.exc_info[1] is not None:
            # Format the traceback now, redacted, and drop the live exception object.
            record.exc_text = redact_text("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        return True


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not any(isinstance(f, SecretFilter) for f in logger.filters):
        logger.addFilter(SecretFilter())
    return logger


def setup(level: str = "INFO") -> None:
    """Print ``mini_agent.*`` records to the console once (safe to call on every reload)."""
    root = logging.getLogger("mini_agent")
    root.setLevel(level.upper())
    if not any(getattr(h, "mini_agent", False) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(FORMAT, datefmt="%H:%M:%S"))
        handler.mini_agent = True  # type: ignore[attr-defined]
        root.addHandler(handler)
