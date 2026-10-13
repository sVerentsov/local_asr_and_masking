"""Демаскирование ответа Claude: метки → оригиналы в нужном падеже, неизвестные метки подсвечиваются."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from transcriber.model import PiiType
from transcriber.nlp import parse
from transcriber.unmask.inflect import inflect_org, inflect_person
from transcriber.unmask.parser import find_labels
from transcriber.vault import Vault

UNKNOWN = "[неизвестное значение]"

_PREP_CASE = {
    **dict.fromkeys(["у", "от", "до", "из", "без", "для", "около", "после", "возле", "кроме", "среди", "против",
                     "вместо", "напротив", "мимо", "ради", "вокруг", "вдоль", "из-за", "из-под", "насчёт",
                     "насчет", "относительно", "ввиду", "со"], "род"),
    **dict.fromkeys(["к", "ко", "по", "благодаря", "согласно", "вопреки", "навстречу"], "дат"),
    **dict.fromkeys(["с", "над", "под", "перед", "между", "за"], "тв"),
    **dict.fromkeys(["о", "об", "обо", "при", "в", "во", "на"], "пр"),
}
_VERB_CASE = {
    **dict.fromkeys(["звонить", "позвонить", "перезвонить", "написать", "писать", "сообщить", "сказать",
                     "передать", "ответить", "помочь", "отправить", "направить", "предложить", "объяснить",
                     "напомнить", "дать", "выслать", "рекомендовать", "порекомендовать", "поручить",
                     "адресовать", "доверять", "звякнуть", "отказать", "пообещать", "обещать", "продать",
                     "показать", "выставить", "оплатить", "вернуть", "уделить"], "дат"),
    **dict.fromkeys(["спросить", "попросить", "уведомить", "предупредить", "пригласить", "проинформировать",
                     "видеть", "увидеть", "знать", "благодарить", "поблагодарить", "поздравить", "назначить",
                     "проконсультировать", "уточнить", "выслушать", "убедить", "заверить", "переубедить",
                     "успокоить", "поддержать", "обслужить", "соединить", "найти", "привлечь"], "вин"),
    **dict.fromkeys(["связаться", "поговорить", "встретиться", "созвониться", "договориться",
                     "переговорить", "работать", "сотрудничать", "общаться", "являться", "стать", "быть"], "тв"),
    **dict.fromkeys(["нет", "отсутствовать", "ждать", "дождаться", "избегать", "бояться"], "род"),
}
_WORD_BEFORE = re.compile(r"([А-Яа-яЁё\-]+)[\s,]*$")


@dataclass
class Highlight:
    start: int
    end: int
    kind: str  # restored | unknown | uncertain
    entity_id: str | None = None


@dataclass
class UnmaskResult:
    text: str
    highlights: list[Highlight] = field(default_factory=list)
    restored: int = 0
    unknown: int = 0
    uncertain: int = 0


def infer_case(text: str, pos: int) -> str | None:
    """Падеж для метки без кода по предлогу или управлению глагола перед ней."""
    before = text[max(0, pos - 60) : pos]
    stripped = before.rstrip(" \t*_\"«")
    if not stripped or re.search(r"[.!?:\n]\s*$", before) or re.search(r"[-–—•]\s*$", stripped):
        return "им"
    m = _WORD_BEFORE.search(stripped)
    if not m:
        return None
    word = m.group(1).lower()
    if word in _PREP_CASE:
        return _PREP_CASE[word]
    for p in parse(word)[:3]:
        if p.normal_form in _VERB_CASE and p.tag.POS in ("VERB", "INFN", "PRED"):
            return _VERB_CASE[p.normal_form]
    return None


def restore(text: str, vault: Vault) -> UnmaskResult:
    res = UnmaskResult(text="")
    out: list[str] = []
    pos = 0
    length = 0
    for f in find_labels(text):
        chunk = text[pos : f.start]
        out.append(chunk)
        length += len(chunk)
        ent = vault.by_label(f.type, f.n)
        kind = "restored"
        if ent is None:
            value, kind = UNKNOWN, "unknown"
            res.unknown += 1
        elif ent.type in (PiiType.PER.value, PiiType.ORG.value) and ent.declinable:
            gcase = f.gcase or infer_case(text, f.start)
            if gcase is None:
                gcase, kind = "им", "uncertain"
                res.uncertain += 1
            value = inflect_person(ent.value, gcase, ent.gender) if ent.type == PiiType.PER else inflect_org(ent.value, gcase)
        else:
            value = ent.value
        if kind == "restored":
            res.restored += 1
        rendered = f"{f.prefix}{value}{f.suffix}"
        out.append(rendered)
        start = length + len(f.prefix)
        res.highlights.append(Highlight(start, start + len(value), kind, ent.id if ent else None))
        length += len(rendered)
        pos = f.end
    out.append(text[pos:])
    res.text = "".join(out)
    return res
