"""Склонение оригиналов в падеж из метки: ФИО — каждое слово отдельно с учётом пола, названия — до главного слова."""

from __future__ import annotations

from transcriber.model import CASE_TO_PYMORPHY
from transcriber.nlp import is_known, parse

_VOWELS = "аеёиоуыэюя"
_INDECL_ENDINGS = ("о", "е", "э", "и", "ы", "у", "ю", "их", "ых", "аго", "яго")

# Окончания мужских и женских фамилий: (суффикс в им. п., {падеж: замена суффикса}).
_MASC = [
    ("ский", {"род": "ского", "дат": "скому", "вин": "ского", "тв": "ским", "пр": "ском"}),
    ("цкий", {"род": "цкого", "дат": "цкому", "вин": "цкого", "тв": "цким", "пр": "цком"}),
    ("ской", {"род": "ского", "дат": "скому", "вин": "ского", "тв": "ским", "пр": "ском"}),
    ("цкой", {"род": "цкого", "дат": "цкому", "вин": "цкого", "тв": "цким", "пр": "цком"}),
    ("ов", {"род": "ова", "дат": "ову", "вин": "ова", "тв": "овым", "пр": "ове"}),
    ("ев", {"род": "ева", "дат": "еву", "вин": "ева", "тв": "евым", "пр": "еве"}),
    ("ёв", {"род": "ёва", "дат": "ёву", "вин": "ёва", "тв": "ёвым", "пр": "ёве"}),
    ("ин", {"род": "ина", "дат": "ину", "вин": "ина", "тв": "иным", "пр": "ине"}),
    ("ын", {"род": "ына", "дат": "ыну", "вин": "ына", "тв": "ыным", "пр": "ыне"}),
    ("ый", {"род": "ого", "дат": "ому", "вин": "ого", "тв": "ым", "пр": "ом"}),
]
_FEMN = [
    ("ская", {"род": "ской", "дат": "ской", "вин": "скую", "тв": "ской", "пр": "ской"}),
    ("цкая", {"род": "цкой", "дат": "цкой", "вин": "цкую", "тв": "цкой", "пр": "цкой"}),
    ("ова", {"род": "овой", "дат": "овой", "вин": "ову", "тв": "овой", "пр": "овой"}),
    ("ева", {"род": "евой", "дат": "евой", "вин": "еву", "тв": "евой", "пр": "евой"}),
    ("ёва", {"род": "ёвой", "дат": "ёвой", "вин": "ёву", "тв": "ёвой", "пр": "ёвой"}),
    ("ина", {"род": "иной", "дат": "иной", "вин": "ину", "тв": "иной", "пр": "иной"}),
    ("ына", {"род": "ыной", "дат": "ыной", "вин": "ыну", "тв": "ыной", "пр": "ыной"}),
    ("ая", {"род": "ой", "дат": "ой", "вин": "ую", "тв": "ой", "пр": "ой"}),
]


def _match_case(src: str, out: str) -> str:
    if src.isupper() and len(src) > 1:
        return out.upper()
    if src[:1].isupper():
        return out[:1].upper() + out[1:]
    return out


def guess_gender(words: list[str]) -> str | None:
    # Сначала слова, которые однозначно имя или отчество (у «Ким» есть и чтение фамилии — его пропускаем).
    for strict in (True, False):
        for w in words:
            ps = parse(w.lower())
            if strict and any("Surn" in p.tag for p in ps):
                continue
            for p in ps:
                if ("Name" in p.tag or "Patr" in p.tag) and p.tag.case == "nomn" and "ms-f" not in p.tag:
                    if p.tag.gender in ("masc", "femn"):
                        return p.tag.gender
    for w in words:
        lw = w.lower()
        if lw.endswith(("ова", "ева", "ёва", "ина", "ына", "ская", "цкая", "овна", "евна", "ична", "инична")):
            return "femn"
        if lw.endswith(("ов", "ев", "ёв", "ин", "ын", "ский", "цкий", "ович", "евич", "ич")):
            return "masc"
    return None


def _role(word: str, gender: str | None) -> tuple[str, object | None]:
    lw = word.lower()
    for tag in ("Patr", "Name"):
        for p in parse(lw):
            if tag in p.tag and p.tag.case == "nomn" and (gender is None or (p.tag.gender == gender or "ms-f" in p.tag)):
                return tag, p
    return "Surn", None


def inflect_surname(word: str, gcase: str, gender: str | None) -> str:
    lw = word.lower()
    if gcase == "им":
        return word
    if lw.endswith(_INDECL_ENDINGS):
        return word
    table = _FEMN if gender == "femn" else _MASC
    for suf, forms in table:
        if lw.endswith(suf):
            return _match_case(word, lw[: -len(suf)] + forms[gcase])
    if gender == "femn":
        return word  # женские фамилии на согласную и прочие не склоняются
    if lw[-1:] not in _VOWELS and is_known(lw):
        for p in parse(lw):
            if "Surn" in p.tag and "Fixd" not in p.tag and p.tag.case == "nomn" and p.tag.gender == "masc":
                inf = p.inflect({CASE_TO_PYMORPHY[gcase]})
                if inf:
                    return _match_case(word, inf.word)
    return word


def inflect_person(value: str, gcase: str, gender: str | None) -> str:
    words = value.split()
    gender = gender or guess_gender(words)
    out = []
    for w in words:
        if "-" in w:
            out.append("-".join(_inflect_person_word(p, gcase, gender) for p in w.split("-")))
        else:
            out.append(_inflect_person_word(w, gcase, gender))
    return " ".join(out)


def _inflect_person_word(word: str, gcase: str, gender: str | None) -> str:
    if gcase == "им" or not word.isalpha():
        return word
    role, p = _role(word, gender)
    if p is not None:
        inf = p.inflect({CASE_TO_PYMORPHY[gcase]})
        return _match_case(word, inf.word) if inf else word
    return inflect_surname(word, gcase, gender)


def inflect_org(value: str, gcase: str) -> str:
    """Склоняет прилагательные и главное существительное; зависимые слова после него не трогает."""
    if gcase == "им":
        return value
    words = value.split()
    out = []
    head_done = False
    for w in words:
        if head_done or not w.isalpha():
            out.append(w)
            continue
        parses = [p for p in parse(w.lower()) if p.tag.case == "nomn" and p.tag.POS in ("NOUN", "ADJF", "PRTF")]
        if not parses:
            out.append(w)
            head_done = True
            continue
        p = parses[0]
        inf = p.inflect({CASE_TO_PYMORPHY[gcase]})
        out.append(_match_case(w, inf.word) if inf else w)
        if p.tag.POS == "NOUN":
            head_done = True
    return " ".join(out)
