"""Structured logging configuration for Tomato Monitor.

Applied once at application startup to configure consistent log formatting
across all modules. Logger names follow the module path convention.
"""
import logging


LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: int = logging.INFO) -> None:
    """Configure Python logging with structured format.

    Applied once during app lifespan startup.
    Sets the root logger format and level.
    Suppresses noisy third-party loggers.

    Args:
        level: Root logger level (default: INFO).
    """
    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT,
        force=True,
    )
    # Suppress noisy uvicorn access logs
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
