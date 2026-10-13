"""Толерантный парсер меток в ответе Claude.

Принимает [ФИО_1:дат], [ФИО_1], ФИО_1, [ФИО 1], \\[ФИО\\_1\\], [**ФИО_1**], [ФИО_1: дат.], а также
маски телефонов и email, даже если Claude переписал разделители.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from transcriber.labels import BRACKET_TYPES, EMAIL_MASK_DOMAIN, phone_mask_number
from transcriber.model import CASES, PiiType

_TYPE_ALIASES = {t: t for t in BRACKET_TYPES} | {"СЧЕТ": PiiType.ACCOUNT.value}
_TYPES_RE = "|".join(re.escape(t) for t in sorted(_TYPE_ALIASES, key=len, reverse=True))
_CASE_RE = "|".join(CASES)
_EMPH = r"(?:\*\*|__|\*|_|`)?"

LABEL_RE = re.compile(
    rf"(?P<open>\\?[\[［【])?\s*(?P<e1>{_EMPH})\s*"
    rf"(?P<type>{_TYPES_RE})(?P<sep>\s*(?:\\?_|\s|-)\s*)(?P<n>\d+)"
    rf"(?:\s*[:：]\s*(?P<case>{_CASE_RE})\.?)?"
    rf"\s*(?P<e2>{_EMPH})\s*(?P<close>\\?[\]］】])?",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(r"(?<![\w+])(?:\+\s?)?[78][\s\-().]*\(?0{3}\)?[\s\-().]*0{3}[\s\-().]*\d{2}[\s\-().]*\d{2}(?!\d)")
_EMAIL_RE = re.compile(rf"\buser\s?(?P<n>\d+)\s?@\s?{re.escape(EMAIL_MASK_DOMAIN)}\b", re.IGNORECASE)
_CODE_RE = re.compile(r"\bDEL-[0-9A-F]{4}\b")


@dataclass
class Found:
    start: int
    end: int
    type: str
    n: int
    gcase: str | None
    prefix: str = ""  # маркеры выделения внутри скобок, которые надо сохранить
    suffix: str = ""


def find_labels(text: str) -> list[Found]:
    out: list[Found] = []
    for m in LABEL_RE.finditer(text):
        typ = _TYPE_ALIASES.get(m.group("type").upper())
        if typ is None:
            continue
        has_open, has_close = bool(m.group("open")), bool(m.group("close"))
        if not has_open and not has_close and "_" not in m.group("sep") and not m.group("type").isupper():
            continue  # «фио 2» в обычном тексте — не метка
        start = m.start("open") if has_open else (m.start("e1") if m.group("e1") else m.start("type"))
        if not has_open and start > 0 and text[start - 1].isalnum():
            continue
        if has_close:
            end = m.end()
        elif m.group("e2"):
            end = m.end("e2")
        else:
            end = m.end("case") if m.group("case") else m.end("n")
        gcase = m.group("case").lower() if m.group("case") else None
        out.append(Found(start, end, typ, int(m.group("n")), gcase, m.group("e1") or "", m.group("e2") or ""))
    for m in _PHONE_RE.finditer(text):
        n = phone_mask_number(re.sub(r"\D", "", m.group()))
        if n:
            out.append(Found(m.start(), m.end(), PiiType.PHONE.value, n, None))
    for m in _EMAIL_RE.finditer(text):
        out.append(Found(m.start(), m.end(), PiiType.EMAIL.value, int(m.group("n")), None))
    out.sort(key=lambda f: f.start)
    dedup: list[Found] = []
    for f in out:
        if dedup and f.start < dedup[-1].end:
            continue
        dedup.append(f)
    return dedup


def find_case_code(text: str) -> str | None:
    m = _CODE_RE.search(text)
    return m.group() if m else None
