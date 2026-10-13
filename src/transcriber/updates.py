"""Проверка обновлений — единственное действие с сетью после установки, только по кнопке."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request

from transcriber import __version__

DEFAULT_URL = os.environ.get("TRANSCRIBER_UPDATE_URL", "")


def _parse(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in v.split("."))


def check() -> dict:
    if not DEFAULT_URL:
        return {"configured": False, "current": __version__,
                "message": "Адрес обновлений не настроен (TRANSCRIBER_UPDATE_URL)"}
    try:
        with urllib.request.urlopen(DEFAULT_URL, timeout=10) as r:  # noqa: S310 — адрес задаёт администратор
            latest = json.load(r)["version"]
    except Exception:  # noqa: BLE001
        return {"configured": True, "current": __version__, "error": "Не удалось получить сведения об обновлении"}
    return {"configured": True, "current": __version__, "latest": latest,
            "available": _parse(latest) > _parse(__version__)}


def apply() -> dict:
    uv = shutil.which("uv")
    if uv is None:
        return {"ok": False, "message": "uv не найден"}
    r = subprocess.run([uv, "tool", "upgrade", "transcriber"], capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        return {"ok": False, "message": "Обновление не удалось"}
    # Модели скачиваются только если изменилась их версия (сверка по models.lock.json).
    subprocess.run([sys.executable, "-m", "transcriber", "models", "download"], capture_output=True, timeout=3600)
    return {"ok": True, "message": "Обновлено. Перезапустите приложение."}
