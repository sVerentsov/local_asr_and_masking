"""Пути, офлайн-режим и логирование без ПД."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

APP = "transcriber"
HOST = "127.0.0.1"
PORT = 7860


def enforce_offline() -> None:
    """После установки приложение не ходит в сеть: модели только из локального каталога."""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
    os.environ["DO_NOT_TRACK"] = "1"


def data_dir() -> Path:
    if env := os.environ.get("TRANSCRIBER_HOME"):
        return Path(env)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP


def models_dir() -> Path:
    return Path(os.environ.get("TRANSCRIBER_MODELS", data_dir() / "models"))


def db_path() -> Path:
    return data_dir() / "cases.db"


def downloads_dir() -> Path:
    if env := os.environ.get("TRANSCRIBER_DOWNLOADS"):
        return Path(env)
    from platformdirs import user_downloads_dir

    return Path(user_downloads_dir())


class _NoContentFilter(logging.Filter):
    """Последний рубеж: в логи не попадают аргументы сообщений, где может оказаться текст."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args and not getattr(record, "safe", False):
            record.msg = f"{record.msg} [аргументы скрыты]"
            record.args = ()
        return True


def setup_logging(level: int = logging.INFO) -> None:
    log_dir = data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_dir / "transcriber.log", encoding="utf-8")
    handler.addFilter(_NoContentFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).disabled = True
