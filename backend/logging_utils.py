from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime

from config import harden_permissions


def configure_logging(logs_dir: Path, *, debug: bool = False) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    harden_permissions(logs_dir, is_dir=True)
    log_path = logs_dir / "app.log"
    local_fallback_dir = logs_dir.parent / "logs_fallback"

    root_logger = logging.getLogger()
    if getattr(root_logger, "_face_attendance_configured", False):
        return log_path

    level = logging.DEBUG if debug else logging.INFO
    root_logger.setLevel(level)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
    except PermissionError:
        local_fallback_dir.mkdir(parents=True, exist_ok=True)
        fallback_log_path = local_fallback_dir / f"app_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        file_handler = RotatingFileHandler(
            fallback_log_path,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        log_path = fallback_log_path
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    root_logger._face_attendance_configured = True  # type: ignore[attr-defined]
    root_logger.info("Logging configured. File: %s", log_path)
    harden_permissions(log_path, is_dir=False)
    return log_path
