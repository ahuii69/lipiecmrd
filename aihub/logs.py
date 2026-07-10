#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import logging.handlers
import os
from pathlib import Path


def setup_logging(
    log_dir: str | None = None,
    log_level: str | None = None,
) -> None:
    """
    Setup centralized logging dla AI-Hub system.

    Args:
        log_dir: Directory dla log files
        log_level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    log_dir = log_dir or os.getenv("LOG_DIR", "logs")
    log_level = log_level or os.getenv("LOG_LEVEL", "INFO")
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # Utwórz root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Clear any existing handlers
    root_logger.handlers.clear()

    # Format dla all logs
    log_format = (
        "%(asctime)s [%(levelname)-8s] %(name)s:%(funcName)s:%(lineno)d - %(message)s"
    )
    formatter = logging.Formatter(log_format)

    # Handler 1: File handler z rotation
    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_path / "aihub.log",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Handler 2: Error file handler (only errors)
    error_handler = logging.handlers.RotatingFileHandler(
        filename=log_path / "aihub.error.log",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=10,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    root_logger.addHandler(error_handler)

    # Handler 3: Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Log startup
    root_logger.info("=" * 80)
    root_logger.info("Logging initialized: %s", log_level)
    root_logger.info("Log directory: %s", log_path)
    root_logger.info("=" * 80)


def get_logger(name: str) -> logging.Logger:
    """Get logger instance dla given name."""
    return logging.getLogger(name)


def setup_test_logging(log_dir: str | None = None) -> None:
    """
    Setup logging for test runs — routes to logs/test.log, not aihub.log.

    Call from conftest.py to keep test output separate from runtime logs.
    """
    log_dir = log_dir or os.getenv("LOG_DIR", "logs")
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()

    log_format = (
        "%(asctime)s [%(levelname)-8s] %(name)s:%(funcName)s:%(lineno)d - %(message)s"
    )
    formatter = logging.Formatter(log_format)

    test_handler = logging.handlers.RotatingFileHandler(
        filename=log_path / "test.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    test_handler.setLevel(logging.DEBUG)
    test_handler.setFormatter(formatter)
    root_logger.addHandler(test_handler)

    # Console at WARNING+ to keep pytest output clean
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    root_logger.info("Test logging initialized → %s/test.log", log_path)
