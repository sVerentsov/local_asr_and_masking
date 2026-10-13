"""Детекция ПД в транскрипте: правила по склеенным потокам, NER и словарь по репликам, морфология."""

from __future__ import annotations

from dataclasses import dataclass, field

from transcriber.labels import protected_ranges
from transcriber.model import ALL_TYPES, Part, PiiType, Span, Transcript
from transcriber.pii import names
from transcriber.pii.rules import ALL_RULES
from transcriber.pii.streams import build_streams

_PRIORITY = {"user": 4, "rule": 3, "ner": 2, "dict": 1}


@dataclass
class DetectSettings:
    types: list[str] = field(default_factory=lambda: list(ALL_TYPES))
    org_dictionary: list[str] = field(default_factory=list)
    use_ner: bool = True


class DetectionError(RuntimeError):
    pass


def detect(tr: Transcript, settings: DetectSettings | None = None) -> list[Span]:
    try:
        return _detect(tr, settings or DetectSettings())
    except Exception as e:  # noqa: BLE001 — любой сбой детекции блокирует экспорт
        raise DetectionError(f"Ошибка детекции ПД: {type(e).__name__}") from e


def _detect(tr: Transcript, st: DetectSettings) -> list[Span]:
    utts = tr.utterances
    texts = [u.text for u in utts]
    cands: list[Span] = []

    for stream in build_streams(utts):
        for rule in ALL_RULES:
            for h in rule(stream.text):
                parts = stream.to_parts(h.start, h.end)
                if not parts:
                    continue
                value = " ".join(texts[p.utt][p.start : p.end] for p in parts)
                cands.append(Span(type=h.type, parts=parts, value=value, source="rule", confidence=h.confidence,
                                  norm=h.norm, declinable=h.declinable))

    need_names = PiiType.PER in st.types or PiiType.ORG in st.types
    tokens = names.tag_utterances(texts) if need_names else [[] for _ in texts]
    if need_names:
        for i, text in enumerate(texts):
            for h in names.find_names(text, tokens[i], use_ner=st.use_ner):
                cands.append(Span(type=h.type, parts=[Part(i, h.start, h.end)], value=text[h.start : h.end],
                                  source=h.source, confidence=h.confidence, declinable=h.declinable))
        cands += _dictionary_orgs(texts, st.org_dictionary)
        cands += _speaker_names(tr, st.use_ner)

    allowed = set(st.types)
    cands = [c for c in cands if c.type in allowed and not _in_protected(c, texts)]
    spans = resolve_overlaps(cands)
    _enrich(spans, tokens)
    return spans


def _in_protected(span: Span, texts: list[str]) -> bool:
    for p in span.parts:
        if p.field != "text":
            continue
        for a, b in protected_ranges(texts[p.utt]):
            if p.start < b and a < p.end:
                return True
    return False


def _overlaps(a: Span, b: Span) -> bool:
    return any(
        pa.utt == pb.utt and pa.field == pb.field and pa.start < pb.end and pb.start < pa.end
        for pa in a.parts
        for pb in b.parts
    )


def resolve_overlaps(cands: list[Span]) -> list[Span]:
    def length(s: Span) -> int:
        return sum(p.end - p.start for p in s.parts)

    order = sorted(cands, key=lambda s: (-_PRIORITY.get(s.source, 0), -length(s), -s.confidence))
    kept: list[Span] = []
    for c in order:
        if not any(_overlaps(c, k) for k in kept):
            kept.append(c)
    kept.sort(key=lambda s: (s.parts[0].utt, s.parts[0].field != "speaker", s.parts[0].start))
    return kept


def _dictionary_orgs(texts: list[str], terms: list[str]) -> list[Span]:
    out = []
    term_lemmas = [[lem for _, lem in names.lemmas(t)] for t in terms if t.strip()]
    term_lemmas = [t for t in term_lemmas if t]
    if not term_lemmas:
        return out
    for i, text in enumerate(texts):
        toks = names.lemmas(text)
        lem = [x for _, x in toks]
        for tl in term_lemmas:
            n = len(tl)
            for j in range(len(lem) - n + 1):
                if lem[j : j + n] == tl:
                    s, e = toks[j][0].start, toks[j + n - 1][0].end
                    out.append(Span(type=PiiType.ORG, parts=[Part(i, s, e)], value=text[s:e], source="user",
                                    confidence=1.0, norm=" ".join(tl)))
    return out


def _speaker_names(tr: Transcript, use_ner: bool) -> list[Span]:
    """Имена в подписях спикеров маскируются как обычный текст; роль («Оператор») остаётся."""
    out = []
    seen: set[str] = set()
    for i, u in enumerate(tr.utterances):
        if u.speaker is None or u.speaker in seen:
            continue
        seen.add(u.speaker)
        display = tr.speaker_name(u.speaker) or ""
        out += speaker_spans(display, i, use_ner)
    return out


def speaker_spans(display: str, utt: int, use_ner: bool = True) -> list[Span]:
    toks = names.tag_utterances([display])[0]
    out = []
    for h in names.find_names(display, toks, use_ner=use_ner):
        if h.type != PiiType.PER:
            continue
        span = Span(type=PiiType.PER, parts=[Part(utt, h.start, h.end, "speaker")],
                    value=display[h.start : h.end], source=h.source, confidence=h.confidence)
        info = names.analyze([t for t in toks if t.start >= h.start and t.end <= h.end], PiiType.PER)
        span.gcase, span.gender, span.norm = "им", info.gender, info.norm
        out.append(span)
    return out


def _enrich(spans: list[Span], tokens) -> None:
    for s in spans:
        if s.type not in (PiiType.PER, PiiType.ORG) or s.norm and not s.declinable:
            continue
        p = s.parts[0]
        if p.field != "text":
            continue
        toks = [t for t in tokens[p.utt] if t.start >= p.start and t.end <= p.end]
        if not toks:
            s.gcase, s.case_flag = "им", True
            s.norm = s.norm or s.value.lower()
            continue
        info = names.analyze(toks, s.type)
        s.gender = info.gender
        if s.source != "user" or not s.norm:
            s.norm = info.norm
        if s.declinable:
            s.gcase = info.gcase or "им"
            s.case_flag = info.gcase is None


def enrich_one(span: Span, text: str) -> None:
    """Морфология для находки, добавленной вручную на экране проверки."""
    p = span.parts[0]
    if p.field != "text":
        span.gcase = "им"
        return
    _enrich([span], {p.utt: names.tag_utterances([text])[0]})
