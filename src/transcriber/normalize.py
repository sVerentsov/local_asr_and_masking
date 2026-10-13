"""Перевод чисел, записанных словами, в цифры.

Нужен и после распознавания, и для импортированных транскриптов: иначе регулярки не найдут
продиктованные номера. Группировка сохраняется: «восемь девятьсот шестнадцать сто двадцать три»
превращается в «8 916 123».
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from transcriber.nlp import parse

_CARD = {
    "ноль": 0, "нуль": 0, "один": 1, "два": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
    "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12,
    "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17,
    "восемнадцать": 18, "девятнадцать": 19, "двадцать": 20, "тридцать": 30, "сорок": 40,
    "пятьдесят": 50, "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
    "сто": 100, "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500, "шестьсот": 600,
    "семьсот": 700, "восемьсот": 800, "девятьсот": 900, "тысяча": 1000,
}
_ORD = {
    "первый": 1, "второй": 2, "третий": 3, "четвертый": 4, "пятый": 5, "шестой": 6, "седьмой": 7,
    "восьмой": 8, "девятый": 9, "десятый": 10, "одиннадцатый": 11, "двенадцатый": 12,
    "тринадцатый": 13, "четырнадцатый": 14, "пятнадцатый": 15, "шестнадцатый": 16,
    "семнадцатый": 17, "восемнадцатый": 18, "девятнадцатый": 19, "двадцатый": 20, "тридцатый": 30,
    "сороковой": 40, "пятидесятый": 50, "шестидесятый": 60, "семидесятый": 70, "восьмидесятый": 80,
    "девяностый": 90, "сотый": 100, "двухсотый": 200, "трехсотый": 300, "четырехсотый": 400,
    "пятисотый": 500, "шестисотый": 600, "семисотый": 700, "восьмисотый": 800, "девятисотый": 900,
    "тысячный": 1000,
}
_ORD_TRIGGERS = re.compile(
    r"^\s*(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря|"
    r"года|году|год|числа)\b",
    re.IGNORECASE,
)
_WORD = re.compile(r"[А-Яа-яЁё]+")
_GAP = re.compile(r"^[\s,\-–]*$")


@dataclass
class _Num:
    start: int
    end: int
    value: int
    ordinal: bool

    @property
    def kind(self) -> str:
        v = self.value
        if v == 1000:
            return "thousand"
        if v >= 100:
            return "hundred"
        if v >= 20:
            return "ten"
        if v >= 10:
            return "teen"
        return "unit"


def _lookup(word: str) -> tuple[int, bool] | None:
    lw = word.lower().replace("ё", "е")
    if lw in _CARD:
        return _CARD[lw], False
    for p in parse(word.lower()):
        nf = p.normal_form.replace("ё", "е")
        if "NUMR" in p.tag and nf in _CARD:
            return _CARD[nf], False
        if "Anum" in p.tag and nf in _ORD:
            return _ORD[nf], True
        if nf == "тысяча" and "NOUN" in p.tag:
            return 1000, False
    return None


_CAN_FOLLOW = {
    "thousand": {"hundred", "ten", "teen", "unit"},
    "hundred": {"ten", "teen", "unit"},
    "ten": {"unit"},
}


def _group(nums: list[_Num]) -> list[tuple[int, int, int, bool, int]]:
    """Группы (start, end, value, ordinal, n_words)."""
    groups: list[list] = []
    cur = None
    for t in nums:
        if cur is not None and not cur["ordinal"]:
            if t.kind == "thousand" and cur["last"] != "thousand" and 0 < cur["value"] < 1000:
                cur.update(value=cur["value"] * 1000, last="thousand", end=t.end, ordinal=t.ordinal)
                cur["n"] += 1
                continue
            if t.value != 0 and cur["value"] != 0 and t.kind in _CAN_FOLLOW.get(cur["last"], ()):
                cur.update(value=cur["value"] + t.value, last=t.kind, end=t.end, ordinal=t.ordinal)
                cur["n"] += 1
                continue
        cur = {"start": t.start, "end": t.end, "value": t.value, "last": t.kind, "ordinal": t.ordinal, "n": 1}
        groups.append(cur)
    return [(g["start"], g["end"], g["value"], g["ordinal"], g["n"]) for g in groups]


def normalize_numbers(text: str) -> str:
    words = []
    for m in _WORD.finditer(text):
        hit = _lookup(m.group())
        words.append((m, hit))

    runs: list[list[_Num]] = []
    prev_end = None
    for m, hit in words:
        if hit is None:
            prev_end = None
            continue
        num = _Num(m.start(), m.end(), hit[0], hit[1])
        if prev_end is not None and runs and _GAP.match(text[prev_end:m.start()]):
            runs[-1].append(num)
        else:
            runs.append([num])
        prev_end = m.end()

    out = []
    pos = 0
    for run in runs:
        single = len(run) == 1
        for start, end, value, ordinal, _n in _group(run):
            if single and (value == 1 or ordinal and not _ORD_TRIGGERS.match(text[end:])):
                continue
            out.append(text[pos:start])
            out.append(str(value))
            pos = end
    out.append(text[pos:])
    return "".join(out)
