"""Диаризация моно через sherpa-onnx: сегментация pyannote 3.0 (int8) и эмбеддинги 3D-Speaker CAM++.

sherpa-onnx везёт собственную сборку onnxruntime, которая конфликтует с пакетом onnxruntime
(его использует onnx-asr) в одном процессе. Поэтому диаризация идёт в отдельном процессе:
аудио передаётся через временный файл в каталоге с правами 700, результат — через stdout.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from transcriber.asr import ModelMissing
from transcriber.audio import SAMPLE_RATE
from transcriber.models import is_installed, model_path

EMBEDDING_FILE = "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
SEGMENTATION_FILE = "model.int8.onnx"
# Порог косинусного расстояния при кластеризации голосов: больше — меньше спикеров.
CLUSTER_THRESHOLD = float(os.environ.get("TRANSCRIBER_DIAR_THRESHOLD", "0.6"))


def _threads() -> int:
    """Вторая половина ядер: диаризация идёт параллельно с распознаванием."""
    if env := os.environ.get("TRANSCRIBER_DIAR_THREADS"):
        return int(env)
    return max(1, (os.cpu_count() or 4) - (os.cpu_count() or 4) // 2)
_lock = threading.Lock()


@dataclass
class Turn:
    start: float
    end: float
    speaker: int


def diarize(wave: np.ndarray, num_speakers: int | None = None, progress=None) -> list[Turn]:
    for m in ("pyannote-segmentation", "speaker-embedding"):
        if not is_installed(m):
            raise ModelMissing(f"Модель {m} не установлена. Запустите: transcriber models download")
    tmp = Path(tempfile.mkdtemp(prefix="transcriber-"))  # mkdtemp создаёт каталог с правами 700
    try:
        audio_file = tmp / "audio.npy"
        np.save(audio_file, np.ascontiguousarray(wave, dtype=np.float32))
        cmd = [sys.executable, "-m", "transcriber.diar.worker", str(audio_file), str(num_speakers or 0)]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # без окна консоли при запуске из ярлыка Windows
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                encoding="utf-8", creationflags=flags)
        result = None
        assert proc.stdout is not None
        for line in proc.stdout:
            if line.startswith("P ") and progress:
                progress(float(line[2:]))
            elif line.startswith("R "):
                result = json.loads(line[2:])
        if proc.wait() != 0 or result is None:
            raise RuntimeError("Сбой диаризации")
        return [Turn(*t) for t in result]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _preload_onnxruntime() -> None:
    """На Windows sherpa-onnx ищет onnxruntime.dll по имени и находит старую системную копию из System32.
    Подгружаем DLL из пакета onnxruntime заранее, чтобы использовалась она."""
    if sys.platform != "win32":
        return
    import ctypes
    import os

    import onnxruntime

    capi = os.path.join(os.path.dirname(onnxruntime.__file__), "capi")
    os.add_dll_directory(capi)
    ctypes.WinDLL(os.path.join(capi, "onnxruntime.dll"))


def diarize_in_process(wave: np.ndarray, num_speakers: int | None = None, progress=None) -> list[Turn]:
    _preload_onnxruntime()
    import sherpa_onnx

    for m in ("pyannote-segmentation", "speaker-embedding"):
        if not is_installed(m):
            raise ModelMissing(f"Модель {m} не установлена. Запустите: transcriber models download")
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(model_path("pyannote-segmentation") / SEGMENTATION_FILE)
            ),
            num_threads=_threads(),
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(model_path("speaker-embedding") / EMBEDDING_FILE), num_threads=_threads()
        ),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=num_speakers or -1, threshold=CLUSTER_THRESHOLD),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise RuntimeError("Некорректная конфигурация диаризации")
    sd = sherpa_onnx.OfflineSpeakerDiarization(config)

    def cb(done: int, total: int) -> int:
        if progress and total:
            progress(done / total)
        return 0

    with _lock:
        result = sd.process(np.ascontiguousarray(wave, dtype=np.float32), callback=cb).sort_by_start_time()
    return [Turn(r.start, r.end, r.speaker) for r in result]


def speaker_at(turns: list[Turn], start: float, end: float) -> int | None:
    best, best_ov = None, 0.0
    totals: dict[int, float] = {}
    for t in turns:
        ov = min(end, t.end) - max(start, t.start)
        if ov > 0:
            totals[t.speaker] = totals.get(t.speaker, 0.0) + ov
    for spk, ov in totals.items():
        if ov > best_ov:
            best, best_ov = spk, ov
    if best is None and turns:
        mid = (start + end) / 2
        best = min(turns, key=lambda t: min(abs(t.start - mid), abs(t.end - mid))).speaker
    return best


__all__ = ["diarize", "speaker_at", "Turn", "SAMPLE_RATE"]
