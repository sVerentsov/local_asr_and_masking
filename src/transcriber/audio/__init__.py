"""Декодирование аудио через PyAV (ffmpeg внутри колёс, системный не нужен)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 16000
MAX_DURATION = 3 * 3600
SUPPORTED = (".mp3", ".m4a", ".wav", ".ogg", ".opus", ".flac", ".webm", ".mp4", ".aac")


class AudioError(ValueError):
    pass


@dataclass
class Audio:
    mono: np.ndarray  # float32, 16 кГц
    channels: list[np.ndarray]  # исходные каналы, ресемплированные в 16 кГц
    duration: float

    @property
    def is_stereo(self) -> bool:
        return len(self.channels) == 2


def decode(path: str) -> Audio:
    import av

    try:
        container = av.open(path)
    except Exception as e:  # noqa: BLE001
        raise AudioError("Не удалось открыть аудиофайл") from e
    with container:
        streams = container.streams.audio
        if not streams:
            raise AudioError("В файле нет аудиодорожки")
        stream = streams[0]
        n_ch = min(stream.codec_context.channels or 1, 2)
        layout = "stereo" if n_ch == 2 else "mono"
        resampler = av.AudioResampler(format="fltp", layout=layout, rate=SAMPLE_RATE)
        chunks: list[np.ndarray] = []
        total = 0
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                arr = out.to_ndarray()
                chunks.append(arr)
                total += arr.shape[1]
                if total > (MAX_DURATION + 60) * SAMPLE_RATE:
                    raise AudioError("Запись длиннее 3 часов")
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray())
    if not chunks:
        raise AudioError("Аудиофайл пуст")
    data = np.concatenate(chunks, axis=1).astype(np.float32)
    channels = [data[i] for i in range(data.shape[0])]
    mono = data.mean(axis=0) if len(channels) > 1 else channels[0]
    return Audio(mono=np.ascontiguousarray(mono), channels=channels, duration=len(mono) / SAMPLE_RATE)


def channels_differ(audio: Audio) -> bool:
    """Стерео с разными собеседниками в каналах: слабая корреляция или сильно разная громкость по участкам."""
    if not audio.is_stereo:
        return False
    a, b = audio.channels
    if np.allclose(a, b, atol=1e-4):
        return False
    win = SAMPLE_RATE  # секундные окна
    n = min(len(a), len(b)) // win
    if n < 3:
        return False
    ea = np.sqrt((a[: n * win].reshape(n, win) ** 2).mean(axis=1)) + 1e-8
    eb = np.sqrt((b[: n * win].reshape(n, win) ** 2).mean(axis=1)) + 1e-8
    ratio = np.abs(np.log10(ea / eb))
    active = (ea > 1e-3) | (eb > 1e-3)
    if active.sum() < 3:
        return False
    # В разведённых каналах в заметной доле окон один канал в 10+ раз громче другого.
    return float((ratio[active] > 1.0).mean()) > 0.3


def gate_crosstalk(own: np.ndarray, other: np.ndarray, ratio: float = 0.3, win_s: float = 0.1) -> np.ndarray:
    """Глушит в канале участки, где собеседник в другом канале заметно громче: эхо и перетекание
    голоса иначе распознаются как реплики второго спикера."""
    win = int(SAMPLE_RATE * win_s)
    n = min(len(own), len(other)) // win
    if n == 0:
        return own
    a = own[: n * win].reshape(n, win)
    b = other[: n * win].reshape(n, win)
    ea = np.sqrt((a**2).mean(axis=1))
    eb = np.sqrt((b**2).mean(axis=1))
    mask = ea >= ratio * eb
    # Расширяем на соседние окна, чтобы не обрезать края слов.
    grown = mask.copy()
    grown[1:] |= mask[:-1]
    grown[:-1] |= mask[1:]
    mask = grown
    out = own.copy()
    out[: n * win] = (a * mask[:, None]).reshape(-1)
    return out
