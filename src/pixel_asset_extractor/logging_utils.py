from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable


def log_file_path(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir) if base_dir is not None else Path.cwd()
    return root / "logs" / "pixel_asset_extractor.log"


def configure_logging(base_dir: str | Path | None = None) -> Path:
    path = log_file_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    if not any(getattr(handler, "baseFilename", None) == str(path) for handler in root_logger.handlers):
        handler = RotatingFileHandler(path, maxBytes=1_048_576, backupCount=5, encoding="utf-8")
        handler.setLevel(logging.INFO)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(threadName)s %(message)s")
        )
        root_logger.addHandler(handler)

    if not any(isinstance(handler, logging.StreamHandler) for handler in root_logger.handlers):
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(logging.WARNING)
        stream_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        root_logger.addHandler(stream_handler)

    return path


def install_excepthook(show_error: Callable[[str, str], None] | None = None) -> Callable[[type[BaseException], BaseException, object], None]:
    logger = logging.getLogger(__name__)

    def excepthook(exc_type: type[BaseException], exc: BaseException, tb: object) -> None:
        logger.exception("Uncaught exception", exc_info=(exc_type, exc, tb))
        if show_error is not None:
            show_error("Unexpected error", f"{exc_type.__name__}: {exc}")

    return excepthook
