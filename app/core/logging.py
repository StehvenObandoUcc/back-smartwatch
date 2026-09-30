import logging
import sys

import structlog

# Claves que nunca deben llegar a los logs (tokens, secretos, datos de salud).
_REDACTED_KEYS = frozenset(
    {
        "authorization",
        "password",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "cookie",
    }
)


def _redact(
    _logger: structlog.types.WrappedLogger, _method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    for key in event_dict.keys() & _REDACTED_KEYS:
        event_dict[key] = "[REDACTED]"
    return event_dict


def configure_logging(level: str, *, json: bool) -> None:
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact,
    ]
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*shared, structlog.processors.format_exc_info, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Uvicorn y SQLAlchemy siguen usando logging estándar; se alinean al mismo nivel.
    logging.basicConfig(level=level, stream=sys.stdout, format="%(message)s")


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
