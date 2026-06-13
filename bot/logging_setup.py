"""Console + rotating-file logging. Uses `rich` for pretty console output if available."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(level: str = "INFO", log_dir: Path | None = None) -> logging.Logger:
    logger = logging.getLogger("egig")
    if logger.handlers:                      # already configured
        return logger
    logger.setLevel(level.upper())
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")

    # console
    try:
        from rich.logging import RichHandler
        console = RichHandler(rich_tracebacks=True, show_path=False, markup=False)
        console.setFormatter(logging.Formatter("%(message)s"))
    except Exception:
        console = logging.StreamHandler()
        console.setFormatter(fmt)
    logger.addHandler(console)

    # file
    if log_dir:
        fh = RotatingFileHandler(Path(log_dir) / "bot.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
        logger.addHandler(fh)
    return logger


def disable_console(logger: logging.Logger) -> None:
    """Remove console handlers (keep file) so they don't corrupt the live TUI."""
    for h in list(logger.handlers):
        if not isinstance(h, RotatingFileHandler):
            logger.removeHandler(h)
