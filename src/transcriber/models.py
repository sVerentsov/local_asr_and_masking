"""Модели: манифест, загрузка при установке со сверкой SHA-256, проверка наличия.

Загрузка идёт только из команды `transcriber models download` (её вызывает install.sh) или по кнопке
обновления. Источник можно заменить зеркалом через TRANSCRIBER_MODELS_MIRROR: тогда файл берётся из
<зеркало>/<имя модели>/<файл>, например с собственного сервера или из GitHub Releases.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from transcriber.config import models_dir

MODELS_VERSION = 1


@dataclass
class ModelFile:
    url: str
    name: str
    extract: bool = False  # tar.bz2 — распаковать содержимое в каталог модели


@dataclass
class ModelSpec:
    name: str
    title: str
    files: list[ModelFile]
    license: str
    required: bool = True


def _hf(repo: str, file: str) -> ModelFile:
    return ModelFile(f"https://huggingface.co/{repo}/resolve/main/{file}", file)


_SHERPA = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
# CAM++ в 2–3 раза быстрее ERes2Net при сопоставимом качестве разделения: диаризация укладывается в бюджет.
_EMB = "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"

MODELS: dict[str, ModelSpec] = {
    "gigaam-v3-e2e-rnnt": ModelSpec(
        "gigaam-v3-e2e-rnnt",
        "GigaAM v3 e2e RNNT (int8)",
        [_hf("istupakov/gigaam-v3-onnx", f) for f in (
            "config.json", "v3_e2e_rnnt_encoder.int8.onnx", "v3_e2e_rnnt_decoder.int8.onnx",
            "v3_e2e_rnnt_joint.int8.onnx", "v3_e2e_rnnt_vocab.txt", "LICENSE.txt",
        )],
        "MIT",
    ),
    "t-one": ModelSpec(
        "t-one",
        "T-one (телефония 8 кГц)",
        [_hf("t-tech/T-one", f) for f in ("config.json", "model.onnx")],
        "Apache-2.0",
        required=False,
    ),
    "silero-vad": ModelSpec(
        "silero-vad",
        "Silero VAD",
        [_hf("istupakov/silero-vad-onnx", f) for f in ("config.json", "silero_vad.onnx", "LICENSE.txt")],
        "MIT",
    ),
    "pyannote-segmentation": ModelSpec(
        "pyannote-segmentation",
        "pyannote segmentation 3.0 (ONNX, sherpa-onnx)",
        [ModelFile(f"{_SHERPA}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
                   "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2", extract=True)],
        "MIT",
    ),
    "speaker-embedding": ModelSpec(
        "speaker-embedding",
        "3D-Speaker CAM++ (эмбеддинги голоса)",
        [ModelFile(f"{_SHERPA}/speaker-recongition-models/{_EMB}", _EMB)],
        "Apache-2.0",
    ),
}


def _lock() -> dict:
    try:
        text = resources.files("transcriber").joinpath("models.lock.json").read_text(encoding="utf-8")
        return json.loads(text)
    except FileNotFoundError:
        return {}


def model_path(name: str) -> Path:
    return models_dir() / name


def is_installed(name: str) -> bool:
    return (model_path(name) / ".complete").exists()


def status() -> dict[str, bool]:
    return {name: is_installed(name) for name in MODELS}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, progress=None) -> None:
    mirror = os.environ.get("TRANSCRIBER_MODELS_MIRROR")
    if mirror:
        url = f"{mirror.rstrip('/')}/{dest.parent.name}/{dest.name}"
    req = urllib.request.Request(url, headers={"User-Agent": "transcriber-installer"})
    with urllib.request.urlopen(req, timeout=60) as r, dest.open("wb") as f:  # noqa: S310 — только https из манифеста
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(dest.name, done, total)


def download(name: str, *, force: bool = False, progress=None) -> Path:
    spec = MODELS[name]
    target = model_path(name)
    lock = _lock().get(name, {})
    if is_installed(name) and not force:
        installed = json.loads((target / ".complete").read_text(encoding="utf-8"))
        if installed.get("version") == lock.get("version", MODELS_VERSION):
            return target
    tmp = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=models_dir().parent if models_dir().parent.exists() else None))
    try:
        hashes = {}
        for mf in spec.files:
            dest = tmp / mf.name
            _download(mf.url, dest, progress)
            digest = _sha256(dest)
            expected = lock.get("sha256", {}).get(mf.name)
            if expected and expected != digest:
                raise RuntimeError(f"Контрольная сумма не совпала: {name}/{mf.name}")
            hashes[mf.name] = digest
            if mf.extract:
                with tarfile.open(dest) as tar:
                    tar.extractall(tmp, filter="data")
                dest.unlink()
                # Содержимое архива лежит в подкаталоге — поднимаем файлы на уровень модели.
                for sub in [p for p in tmp.iterdir() if p.is_dir()]:
                    for f in sub.iterdir():
                        shutil.move(str(f), tmp / f.name)
                    sub.rmdir()
        (tmp / ".complete").write_text(json.dumps({"version": lock.get("version", MODELS_VERSION),
                                                   "sha256": hashes}, indent=1), encoding="utf-8")
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp), target)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    return target


def write_lock(path: Path) -> None:
    """Для сопровождающего: зафиксировать хэши скачанных моделей в models.lock.json."""
    out = {}
    for name in MODELS:
        info = model_path(name) / ".complete"
        if info.exists():
            data = json.loads(info.read_text(encoding="utf-8"))
            out[name] = {"version": data.get("version", MODELS_VERSION), "sha256": data["sha256"]}
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
