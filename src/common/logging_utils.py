"""Structured logging configuration for agents and pipeline."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional


def setup_logger(name: str = "ev_fleet_optimizer", log_file: Optional[Path] = None, level: int = logging.INFO) -> logging.Logger:
    """Configures and returns a logger instance with formatting."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
