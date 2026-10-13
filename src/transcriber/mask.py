"""Маскирование: находки → сущности словаря дела → текст с метками."""

from __future__ import annotations

from dataclasses import dataclass

from transcriber.model import Span, Transcript
from transcriber.normalize import normalize_numbers
from transcriber.pii.detect import DetectSettings, detect
from transcriber.vault import Vault

CONTINUATION = "[…]"


@dataclass
class MaskedUtterance:
    speaker: str | None
    start: float | None
    text: str


def normalize_transcript(tr: Transcript) -> None:
    for u in tr.utterances:
        u.text = normalize_numbers(u.text)


def assign_entities(spans: list[Span], vault: Vault) -> None:
    for s in spans:
        if not s.entity_id or vault.get(s.entity_id) is None:
            vault.match_or_create(s)


def detect_and_assign(tr: Transcript, vault: Vault, settings: DetectSettings | None = None) -> None:
    tr.spans = detect(tr, settings)
    assign_entities(tr.spans, vault)
    tr.reviewed = False


def label_for(span: Span, vault: Vault) -> str:
    ent = vault.get(span.entity_id)
    if ent is None:
        raise RuntimeError("Находка не привязана к словарю дела")
    gcase = span.gcase if ent.declinable else None
    if span.parts and span.parts[0].field == "speaker":
        gcase = "им" if ent.declinable else None
    return vault.label(ent, gcase)


def _apply(text: str, repl: list[tuple[int, int, str]]) -> str:
    out, pos = [], 0
    for s, e, r in sorted(repl):
        if s < pos:
            raise RuntimeError("Пересекающиеся маски")
        out.append(text[pos:s])
        out.append(r)
        pos = e
    out.append(text[pos:])
    return "".join(out)


def render(tr: Transcript, vault: Vault) -> list[MaskedUtterance]:
    text_repl: dict[int, list] = {}
    speaker_repl: dict[str, list] = {}
    for span in tr.spans:
        label = label_for(span, vault)
        for k, p in enumerate(span.parts):
            r = label if k == 0 else CONTINUATION
            if p.field == "speaker":
                raw = tr.utterances[p.utt].speaker
                speaker_repl.setdefault(raw, []).append((p.start, p.end, r))
            else:
                text_repl.setdefault(p.utt, []).append((p.start, p.end, r))

    speakers: dict[str | None, str | None] = {}
    out = []
    for i, u in enumerate(tr.utterances):
        if u.speaker not in speakers:
            disp = tr.speaker_name(u.speaker)
            speakers[u.speaker] = _apply(disp, speaker_repl.get(u.speaker, [])) if disp else disp
        out.append(MaskedUtterance(speakers[u.speaker], u.start, _apply(u.text, text_repl.get(i, []))))
    return out


def fmt_time(sec: float | None) -> str:
    if sec is None:
        return ""
    s = int(sec)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def masked_lines(tr: Transcript, vault: Vault) -> list[str]:
    lines = []
    for mu in render(tr, vault):
        head = mu.speaker or ""
        if mu.start is not None:
            head = f"{head} [{fmt_time(mu.start)}]".strip()
        lines.append(f"{head}: {mu.text}" if head else mu.text)
    return lines
