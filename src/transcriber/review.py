"""Операции экрана проверки: снять маску, добавить по выделению, объединить, переименовать спикера,
кандидаты на объединение и поиск немаскированных вхождений известных значений."""

from __future__ import annotations

import re
from dataclasses import dataclass

from transcriber.model import Part, PiiType, Span, Transcript
from transcriber.nlp import parse, tokenize
from transcriber.pii.detect import enrich_one, resolve_overlaps, speaker_spans
from transcriber.vault import Vault


def remove_span(tr: Transcript, span_id: str, *, all_of_entity: bool = False) -> int:
    target = next((s for s in tr.spans if s.id == span_id), None)
    if target is None:
        return 0
    if all_of_entity and target.entity_id:
        before = len(tr.spans)
        tr.spans = [s for s in tr.spans if s.entity_id != target.entity_id]
        removed = before - len(tr.spans)
    else:
        tr.spans = [s for s in tr.spans if s.id != span_id]
        removed = 1
    eid = target.entity_id
    if eid and eid not in tr.dismissed and not any(s.entity_id == eid for s in tr.spans):
        tr.dismissed.append(eid)
    tr.reviewed = False
    return removed


def _lemma(word: str) -> str:
    return parse(word.lower())[0].normal_form if word.isalpha() else word.lower()


def _lemma_index(tr: Transcript) -> list[tuple[list, list[str]]]:
    out = []
    for u in tr.utterances:
        toks = tokenize(u.text)
        out.append((toks, [_lemma(t.text) for t in toks]))
    return out


def find_occurrences(tr: Transcript, value: str, type_: str, index=None) -> list[Part]:
    """Все вхождения значения в тексте реплик. Для ФИО и организаций — с учётом словоформ."""
    out: list[Part] = []
    if type_ in (PiiType.PER, PiiType.ORG):
        want = [_lemma(t.text) for t in tokenize(value)]
        if not want:
            return out
        n = len(want)
        for i, (toks, lem) in enumerate(index or _lemma_index(tr)):
            for j in range(len(toks) - n + 1):
                if lem[j] == want[0] and lem[j : j + n] == want:
                    out.append(Part(i, toks[j].start, toks[j + n - 1].end))
    else:
        rx = re.compile(rf"(?<!\w){re.escape(value)}(?!\w)", re.IGNORECASE)
        for i, u in enumerate(tr.utterances):
            out += [Part(i, m.start(), m.end()) for m in rx.finditer(u.text)]
    return out


def add_span(tr: Transcript, vault: Vault, utt: int, start: int, end: int, type_: str, *,
             all_occurrences: bool = True) -> list[Span]:
    text = tr.utterances[utt].text
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start >= end:
        raise ValueError("Пустое выделение")
    value = text[start:end]
    parts = [Part(utt, start, end)]
    if all_occurrences:
        parts += [p for p in find_occurrences(tr, value, type_) if not (p.utt == utt and p.start == start)]

    new_spans: list[Span] = []
    entity_id = None
    for p in parts:
        span = Span(type=type_, parts=[p], value=tr.utterances[p.utt].text[p.start : p.end], source="user",
                    declinable=type_ in (PiiType.PER, PiiType.ORG) and not re.search(r"[«\"„“]", value))
        if span.declinable:
            enrich_one(span, tr.utterances[p.utt].text)
        if entity_id is None:
            vault.match_or_create(span)
            entity_id = span.entity_id
        else:
            span.entity_id = entity_id
            ent = vault.get(entity_id)
            if ent and span.value not in ent.variants:
                ent.variants.append(span.value)
        new_spans.append(span)

    # Ручная маска важнее автоматической: пересекающиеся находки уступают ей.
    tr.spans = resolve_overlaps(new_spans + tr.spans)
    if entity_id in tr.dismissed:
        tr.dismissed.remove(entity_id)
    tr.reviewed = False
    return [s for s in new_spans if s in tr.spans]


def set_case(tr: Transcript, span_id: str, gcase: str) -> None:
    for s in tr.spans:
        if s.id == span_id:
            s.gcase, s.case_flag = gcase, False


def merge_entities(tr_list: list[Transcript], vault: Vault, keep_id: str, drop_id: str) -> None:
    keep = vault.merge(keep_id, drop_id)
    for tr in tr_list:
        for s in tr.spans:
            if s.entity_id == drop_id:
                s.entity_id = keep.id
        tr.reviewed = False


def rename_speaker(tr: Transcript, vault: Vault, raw: str, display: str) -> None:
    tr.speakers[raw] = display.strip() or raw
    tr.spans = [s for s in tr.spans if not (s.parts[0].field == "speaker" and tr.utterances[s.parts[0].utt].speaker == raw)]
    first = next((i for i, u in enumerate(tr.utterances) if u.speaker == raw), None)
    if first is not None:
        for s in speaker_spans(tr.speakers[raw], first):
            vault.match_or_create(s)
            tr.spans.append(s)
    tr.reviewed = False


# ---------------------------------------------------------------- подсказки


@dataclass
class MergeSuggestion:
    a: str
    b: str
    reason: str


def _lev(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def suggest_merges(vault: Vault, entity_ids: set[str]) -> list[MergeSuggestion]:
    """Варианты одной фамилии от ASR (расстояние до 2) и пересекающиеся ФИО — только предложения."""
    people = [e for e in vault.live() if e.type == PiiType.PER and e.id in entity_ids]
    out = []
    for i, a in enumerate(people):
        for b in people[i + 1 :]:
            ta, tb = a.key.split(), b.key.split()
            if set(ta) & set(tb):
                out.append(MergeSuggestion(a.id, b.id, "общая часть имени"))
                continue
            close = [(x, y) for x in ta for y in tb if min(len(x), len(y)) >= 4 and _lev(x, y) <= 2]
            if close:
                out.append(MergeSuggestion(a.id, b.id, f"похожие написания: {close[0][0]} / {close[0][1]}"))
    return out


@dataclass
class Leak:
    entity_id: str
    parts: list[Part]


def unmasked_occurrences(tr: Transcript, vault: Vault) -> list[Leak]:
    """Известные значения дела, которые встречаются в тексте без маски."""
    masked = [(p.utt, p.start, p.end) for s in tr.spans for p in s.parts if p.field == "text"]

    def is_masked(p: Part) -> bool:
        return any(u == p.utt and a < p.end and p.start < b for u, a, b in masked)

    out = []
    index = _lemma_index(tr)
    dismissed = {vault.get(e).id for e in tr.dismissed if vault.get(e)}
    for ent in vault.live():
        if ent.id in dismissed:
            continue
        found: list[Part] = []
        values = {ent.value, *ent.variants}
        for v in values:
            if len(v) < 3:
                continue
            for p in find_occurrences(tr, v, ent.type, index):
                if not is_masked(p) and not any((q.utt, q.start) == (p.utt, p.start) for q in found):
                    found.append(p)
        if found:
            out.append(Leak(ent.id, found))
    return out


def mask_leak(tr: Transcript, vault: Vault, leak_entity: str, parts: list[Part]) -> None:
    ent = vault.get(leak_entity)
    if ent is None:
        return
    new = []
    for p in parts:
        text = tr.utterances[p.utt].text
        span = Span(type=ent.type, parts=[p], value=text[p.start : p.end], source="user",
                    declinable=ent.declinable, entity_id=ent.id)
        if span.declinable:
            enrich_one(span, text)
            span.entity_id = ent.id
        new.append(span)
    tr.spans = resolve_overlaps(new + tr.spans)
    tr.reviewed = False
