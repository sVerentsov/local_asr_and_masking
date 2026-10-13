"""Конвейер: аудио → реплики со спикерами и таймкодами; текст → маскирование в рамках дела."""

from __future__ import annotations

import threading
from collections.abc import Callable

from transcriber import asr, diar
from transcriber.audio import Audio, channels_differ, decode, gate_crosstalk
from transcriber.mask import detect_and_assign, normalize_transcript
from transcriber.model import Transcript, Utterance, new_id
from transcriber.pii.detect import DetectSettings
from transcriber.vault import Vault

Progress = Callable[[str, float], None]
MERGE_GAP_S = 1.0


def _noop(stage: str, frac: float) -> None:
    pass


def _tokens_text(tokens: list[str]) -> str:
    return "".join(tokens).strip()


def _split_by_speakers(seg: asr.Segment, turns: list[diar.Turn]) -> list[tuple[int | None, float, float, str]]:
    """Делит сегмент ASR по сменам спикера, если в нём говорили двое (по времени токенов)."""
    whole = diar.speaker_at(turns, seg.start, seg.end)
    if not seg.tokens or len(seg.tokens) != len(seg.timestamps):
        return [(whole, seg.start, seg.end, seg.text)]
    runs: list[list] = []
    for tok, ts in zip(seg.tokens, seg.timestamps):
        spk = diar.speaker_at(turns, ts, ts + 0.08)
        spk = whole if spk is None else spk
        # Новое слово начинается с пробела — менять спикера можно только на границе слов.
        if runs and (runs[-1][0] == spk or not tok.startswith(" ")):
            runs[-1][3].append(tok)
            runs[-1][2] = ts
        else:
            runs.append([spk, ts, ts, [tok]])
    if len(runs) == 1:
        return [(runs[0][0], seg.start, seg.end, seg.text)]
    out = []
    for i, (spk, a, _b, toks) in enumerate(runs):
        start = seg.start if i == 0 else a
        end = seg.end if i == len(runs) - 1 else runs[i + 1][1]
        text = _tokens_text(toks)
        if text:
            out.append((spk, start, end, text))
    return out


def _speaker_names(order: list) -> dict:
    return {spk: f"Спикер {i + 1}" for i, spk in enumerate(order)}


def _merge(items: list[tuple[str | None, float, float, str]]) -> list[Utterance]:
    out: list[Utterance] = []
    for spk, start, end, text in sorted(items, key=lambda x: x[1]):
        if out and out[-1].speaker == spk and start - (out[-1].end or start) <= MERGE_GAP_S:
            out[-1].text = f"{out[-1].text} {text}"
            out[-1].end = end
        else:
            out.append(Utterance(text, spk, round(start, 2), round(end, 2)))
    return out


def audio_to_utterances(audio: Audio, engine: str = "gigaam", num_speakers: int | None = None,
                        progress: Progress = _noop) -> list[Utterance]:
    if channels_differ(audio) and num_speakers in (None, 2):
        items = []
        a, b = audio.channels
        for ch, wave in enumerate((gate_crosstalk(a, b), gate_crosstalk(b, a))):
            def p(f, ch=ch):
                progress("Распознавание", (ch + f) / 2)
            for seg in asr.transcribe(wave, engine, p):
                items.append((f"Спикер {ch + 1}", seg.start, seg.end, seg.text))
        return _merge(items)

    if num_speakers == 1:
        segs = asr.transcribe(audio.mono, engine, lambda f: progress("Распознавание", f))
        return _merge([("Спикер 1", s.start, s.end, s.text) for s in segs])

    # Диаризация идёт в отдельном процессе параллельно с распознаванием: время — максимум, а не сумма.
    diar_result: dict = {}

    def run_diar() -> None:
        try:
            diar_result["turns"] = diar.diarize(audio.mono, num_speakers)
        except BaseException as e:  # noqa: BLE001 — передаём в основной поток
            diar_result["error"] = e

    worker = threading.Thread(target=run_diar, name="diarization", daemon=True)
    worker.start()
    segs = asr.transcribe(audio.mono, engine, lambda f: progress("Распознавание", f))
    progress("Разделение по спикерам", 0.0)
    worker.join()
    if "error" in diar_result:
        raise diar_result["error"]
    progress("Разделение по спикерам", 1.0)
    if not segs:
        return []
    turns = diar_result["turns"]
    pieces = [piece for s in segs for piece in _split_by_speakers(s, turns)]
    order: list = []
    for spk, *_ in pieces:
        if spk not in order:
            order.append(spk)
    names = _speaker_names(order)
    return _merge([(names[spk], a, b, t) for spk, a, b, t in pieces])


def transcribe_file(path: str, engine: str = "gigaam", num_speakers: int | None = None,
                    progress: Progress = _noop) -> tuple[list[Utterance], float]:
    progress("Декодирование", 0.0)
    audio = decode(path)
    progress("Декодирование", 1.0)
    return audio_to_utterances(audio, engine, num_speakers, progress), audio.duration


def build_transcript(utts: list[Utterance], vault: Vault, *, title: str, source_kind: str,
                     duration: float | None = None, settings: DetectSettings | None = None,
                     progress: Progress = _noop) -> Transcript:
    tr = Transcript(id=new_id(), title=title, utterances=utts, duration=duration, source_kind=source_kind)
    progress("Поиск персональных данных", 0.0)
    normalize_transcript(tr)
    detect_and_assign(tr, vault, settings)
    progress("Поиск персональных данных", 1.0)
    return tr
