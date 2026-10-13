"""Склейка соседних реплик одного спикера.

Номера диктуют группами и с перебивками («восемь девятьсот…» — «угу» — «…сорок пять»), поэтому
правила запускаются по тексту, где реплики спикера склеены через короткие реплики собеседника.
Каждая реплика входит ровно в один поток; находки переводятся обратно в фрагменты реплик.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from transcriber.model import Part, Utterance

MAX_INTERRUPTION_WORDS = 4
MAX_INTERRUPTIONS = 2


@dataclass
class _Seg:
    utt: int
    offset: int
    length: int


@dataclass
class Stream:
    text: str = ""
    segs: list[_Seg] = field(default_factory=list)
    last_utt: int = -1

    def add(self, utt: int, text: str) -> None:
        if self.segs:
            self.text += " "
        self.segs.append(_Seg(utt, len(self.text), len(text)))
        self.text += text
        self.last_utt = utt

    def to_parts(self, start: int, end: int) -> list[Part]:
        parts = []
        for s in self.segs:
            a, b = max(start, s.offset), min(end, s.offset + s.length)
            if a < b:
                parts.append(Part(s.utt, a - s.offset, b - s.offset))
        return parts


def build_streams(utts: list[Utterance]) -> list[Stream]:
    streams: list[Stream] = []
    open_by_speaker: dict[str | None, Stream] = {}
    for i, u in enumerate(utts):
        st = open_by_speaker.get(u.speaker)
        if st is not None and _short_gap(utts, st.last_utt, i):
            st.add(i, u.text)
            continue
        st = Stream()
        st.add(i, u.text)
        streams.append(st)
        open_by_speaker[u.speaker] = st
    return streams


def _short_gap(utts: list[Utterance], last: int, cur: int) -> bool:
    between = utts[last + 1 : cur]
    if len(between) > MAX_INTERRUPTIONS:
        return False
    return all(len(u.text.split()) <= MAX_INTERRUPTION_WORDS for u in between)
