"""Распознавание речи через onnx-asr: GigaAM v3 e2e RNNT по умолчанию, T-one как опция. Нарезка по Silero VAD."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field

import numpy as np

from transcriber.audio import SAMPLE_RATE
from transcriber.models import is_installed, model_path

ENGINES = {
    "gigaam": ("gigaam-v3-e2e-rnnt", "gigaam-v3-e2e-rnnt", "int8"),
    "tone": ("t-tech/t-one", "t-one", None),
}
MAX_SEGMENT_S = 30


def asr_threads() -> int:
    """Половина ядер: вторую половину в это время занимает диаризация в отдельном процессе."""
    if env := os.environ.get("TRANSCRIBER_ASR_THREADS"):
        return int(env)
    return max(1, (os.cpu_count() or 4) // 2)


class ModelMissing(RuntimeError):
    pass


@dataclass
class Segment:
    start: float
    end: float
    text: str
    tokens: list[str] = field(default_factory=list)
    timestamps: list[float] = field(default_factory=list)  # абсолютное время токенов, с


_cache: dict[str, object] = {}
_lock = threading.Lock()


def _load(engine: str):
    import onnx_asr

    with _lock:
        if engine not in _cache:
            name, folder, quant = ENGINES[engine]
            for m in (folder, "silero-vad"):
                if not is_installed(m):
                    raise ModelMissing(f"Модель {m} не установлена. Запустите: transcriber models download")
            import onnxruntime as rt

            opts = rt.SessionOptions()
            opts.intra_op_num_threads = asr_threads()
            model = onnx_asr.load_model(name, model_path(folder), quantization=quant, sess_options=opts)
            vad = onnx_asr.load_vad("silero", model_path("silero-vad"))
            _cache[engine] = model.with_vad(
                vad,
                max_speech_duration_s=MAX_SEGMENT_S,
                min_silence_duration_ms=300,
                speech_pad_ms=100,
                batch_size=4,
            ).with_timestamps()
    return _cache[engine]


def transcribe(wave: np.ndarray, engine: str = "gigaam", progress=None) -> list[Segment]:
    model = _load(engine)
    duration = len(wave) / SAMPLE_RATE or 1.0
    out: list[Segment] = []
    for seg in model.recognize(wave, sample_rate=SAMPLE_RATE):
        text = (seg.text or "").strip()
        if text:
            tokens = list(getattr(seg, "tokens", None) or [])
            ts = [seg.start + float(t) for t in (getattr(seg, "timestamps", None) or [])]
            out.append(Segment(float(seg.start), float(seg.end), text, tokens, ts))
        if progress:
            progress(min(1.0, float(seg.end) / duration))
    return out
