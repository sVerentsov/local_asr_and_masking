import json
import wave

import numpy as np
import pytest

from transcriber import models
from transcriber.audio import SAMPLE_RATE, channels_differ, decode, gate_crosstalk
from transcriber.evaluate import evaluate_records


def _write_wav(path, data: np.ndarray, channels: int = 1):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes((np.clip(data, -1, 1) * 32767).astype(np.int16).tobytes())


def test_decode_and_channels(tmp_path):
    t = np.arange(SAMPLE_RATE * 6) / SAMPLE_RATE
    tone = 0.3 * np.sin(2 * np.pi * 440 * t)
    gate = (np.floor(t) % 2 == 0).astype(float)
    left, right = tone * gate, tone * (1 - gate) + 0.002 * tone * gate
    stereo = np.stack([left, right], axis=1).reshape(-1)
    _write_wav(tmp_path / "s.wav", stereo, 2)
    a = decode(str(tmp_path / "s.wav"))
    assert a.is_stereo and abs(a.duration - 6) < 0.05
    assert channels_differ(a)
    gated = gate_crosstalk(a.channels[1], a.channels[0])
    assert np.abs(gated[: SAMPLE_RATE // 2]).max() == 0  # эхо в правом канале заглушено

    _write_wav(tmp_path / "m.wav", tone)
    assert not channels_differ(decode(str(tmp_path / "m.wav")))


def test_decode_rejects_garbage(tmp_path):
    from transcriber.audio import AudioError

    p = tmp_path / "x.mp3"
    p.write_bytes(b"not audio at all")
    with pytest.raises(AudioError):
        decode(str(p))


def test_evaluate_metrics():
    rec = {
        "id": "c1",
        "utterances": [
            {"speaker": "Оператор", "text": "Перезвоните Петрову Ивану по номеру 8 916 123 45 67."},
            {"speaker": "Клиент", "text": "Карта 4111 1111 1111 1111, ИНН 7707083893."},
        ],
        "pii": [
            {"type": "ФИО", "value": "Петрову Ивану", "utt": 0, "case": "дат"},
            {"type": "ТЕЛЕФОН", "value": "8 916 123 45 67", "utt": 0},
            {"type": "КАРТА", "value": "4111 1111 1111 1111", "utt": 1},
            {"type": "ИНН", "value": "7707083893", "utt": 1},
        ],
    }
    report = evaluate_records([json.loads(json.dumps(rec))])
    assert report["recall_format"] == 1.0
    assert report["recall_names"] == 1.0
    assert report["case_accuracy_masking"] == 1.0
    assert report["leaks_before_review"] == 0
    assert report["idempotency_new_spans"] == 0


needs_models = pytest.mark.skipif(
    not all(models.is_installed(m) for m in ("gigaam-v3-e2e-rnnt", "silero-vad", "pyannote-segmentation",
                                             "speaker-embedding")),
    reason="модели не установлены (transcriber models download)",
)


@pytest.mark.slow
@needs_models
def test_asr_on_silence_and_tone(tmp_path):
    from transcriber.pipeline import transcribe_file

    t = np.arange(SAMPLE_RATE * 3) / SAMPLE_RATE
    _write_wav(tmp_path / "tone.wav", 0.01 * np.sin(2 * np.pi * 300 * t))
    utts, dur = transcribe_file(str(tmp_path / "tone.wav"))
    assert abs(dur - 3) < 0.05
    assert all(u.speaker for u in utts)
