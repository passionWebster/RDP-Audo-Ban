"""Centralised logging setup.

Provides ``get_logger()`` which configures the application-wide logger
on first call and returns the same instance on subsequent calls.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

# Module-level reference – populated by setup_logging(), accessed by get_logger().
_logger: logging.Logger | None = None


def setup_logging(config: object) -> logging.Logger:
    """Initialise the RDP-Auto-Ban logger.

    Creates the log directory if needed, attaches a rotating file handler
    and a console handler, and stores the logger globally for retrieval
    via ``get_logger()``.

    Parameters
    ----------
    config:
        An object / module with the following attributes (as provided by
        ``src.config.Config``):
        - log_dir : Path
        - log_level : str
        - log_max_bytes : int
        - log_backup_count : int
    """
    global _logger

    log_dir = Path(config.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "rdp_auto_ban.log"

    logger = logging.getLogger("RDP-Auto-Ban")
    logger.setLevel(getattr(logging, config.log_level))

    # Guard against double-initialisation.
    if logger.handlers:
        _logger = logger
        return logger

    # Formatter
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Rotating file handler — 10 MB × 5 backups by default.
    file_handler = logging.handlers.RotatingFileHandler(
        str(log_file),
        maxBytes=config.log_max_bytes,
        backupCount=config.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Console handler — harmless in service mode (stdout → nul).
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    # Force UTF-8 on the stream to avoid GBK encoding errors on Chinese Windows.
    console_handler.stream.reconfigure(encoding="utf-8", errors="replace")
    logger.addHandler(console_handler)

    _logger = logger
    return logger


def get_logger() -> logging.Logger:
    """Return the pre-configured application logger.

    Raises ``RuntimeError`` if ``setup_logging()`` hasn't been called yet.
    """
    if _logger is None:
        raise RuntimeError("Logger not initialised — call setup_logging() first")
    return _logger
