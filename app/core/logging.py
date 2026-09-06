"""Structured logging configuration powered by structlog.

Every financial mutation is logged with a ``request_id``, ``merchant_id`` and
an actionable ``event`` for end-to-end auditability and reconciliation.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.typing import EventDict, WrappedLogger

from app.core.config import settings


def _add_request_id(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Attach the current request id from the logging context if present."""
    if hasattr(structlog.contextvars, "get_contextvars"):
        ctx = structlog.contextvars.bind_contextvars  # noqa: F841

    request_id = event_dict.get("request_id", None)
    if not request_id:
        request_id = structlog.contextvars.get_contextvars().get("request_id")
    if request_id:
        event_dict["request_id"] = request_id
    return event_dict


def setup_logging() -> None:
    """Configure structlog as the primary logging backend."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.DEBUG if settings.debug else logging.INFO,
    )

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _add_request_id,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.is_production:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(
            structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
        )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if settings.debug else logging.INFO
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "payment_gateway") -> structlog.stdlib.BoundLogger:
    """Return a module-scoped structlog logger."""
    return structlog.get_logger(name)


# Ensure logging is configured at import time for worker + app processes.
setup_logging()
