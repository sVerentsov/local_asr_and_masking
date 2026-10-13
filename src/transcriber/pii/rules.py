"""Правила для форматных ПД: телефоны, email, карты, счета, паспорта, СНИЛС, ИНН, адреса, даты рождения, коды.

Каждое правило получает текст потока и возвращает находки в его координатах.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from transcriber.model import PiiType
from transcriber.pii.checksums import inn_ok, luhn_ok, snils_ok


@dataclass
class Hit:
    start: int
    end: int
    type: str
    confidence: float = 1.0
    norm: str | None = None
    declinable: bool = True


def _ctx(text: str, start: int, width: int = 60) -> str:
    return text[max(0, start - width) : start].lower()


# ---------------------------------------------------------------- числа

_RUN = re.compile(r"(?<![\w+])(?:\+\s?)?\(?\d+(?:(?:[ \-().]{1,3}|, )\d+)*(?![\w])")
_GROUP = re.compile(r"\d+")

_CTX_PHONE = re.compile(r"телефон|номер|позвон|звон|мобильн|сотов|whatsapp|ватсап|телеграм|набер|набр")
_CTX_PASSPORT = re.compile(r"паспорт|серия|серии")
_CTX_INN = re.compile(r"\bинн\b|инн\s|налогов")
_CTX_SNILS = re.compile(r"снилс|страхов")
_CTX_CARD = re.compile(r"карт|visa|виза|мастеркард|mastercard|мир\b")
_CTX_ACCOUNT = re.compile(r"сч[её]т|р/с|расч[её]тн")


def _classify(d: str, ctx: str, plus: bool) -> tuple[str, float] | None:
    n = len(d)
    if n == 11 and (plus or d[0] in "78"):
        if _CTX_SNILS.search(ctx) and snils_ok(d):
            return PiiType.SNILS, 1.0
        return PiiType.PHONE, 1.0
    if n == 11:
        if snils_ok(d):
            return PiiType.SNILS, 1.0 if _CTX_SNILS.search(ctx) else 0.8
        return None
    if n == 10:
        if _CTX_PASSPORT.search(ctx[-40:]):
            return PiiType.PASSPORT, 1.0
        if _CTX_INN.search(ctx) and inn_ok(d):
            return PiiType.INN, 1.0
        if d[0] in "3489" or _CTX_PHONE.search(ctx):
            return PiiType.PHONE, 0.9 if d[0] == "9" else 0.6
        if inn_ok(d):
            return PiiType.INN, 0.7
        return None
    if n == 12:
        if inn_ok(d):
            return PiiType.INN, 1.0 if _CTX_INN.search(ctx) else 0.8
        return None
    if 13 <= n <= 19:
        if luhn_ok(d):
            return PiiType.CARD, 1.0
        if n == 16 and _CTX_CARD.search(ctx):
            return PiiType.CARD, 0.6
        return None
    if n == 20:
        return PiiType.ACCOUNT, 1.0 if _CTX_ACCOUNT.search(ctx) else 0.9
    return None


def number_hits(text: str) -> list[Hit]:
    hits: list[Hit] = []
    for run in _RUN.finditer(text):
        groups = list(_GROUP.finditer(run.group()))
        if sum(len(g.group()) for g in groups) < 10:
            continue
        base = run.start()
        plus = run.group().startswith("+")
        # Перебираем окна по границам групп, длинные первыми, без пересечений.
        cands = []
        for i in range(len(groups)):
            for j in range(i, len(groups)):
                d = "".join(g.group() for g in groups[i : j + 1])
                if len(d) < 10 or len(d) > 20:
                    continue
                s = base + groups[i].start()
                e = base + groups[j].end()
                p = plus and i == 0
                if p:
                    s = base
                cls = _classify(d, _ctx(text, s), p)
                if cls:
                    cands.append((j - i, len(d), s, e, cls))
        cands.sort(key=lambda c: (-c[1], c[2]))
        taken: list[tuple[int, int]] = []
        for _, _, s, e, (typ, conf) in cands:
            if any(s < b and a < e for a, b in taken):
                continue
            taken.append((s, e))
            hits.append(Hit(s, e, typ, conf))
    return hits


_PASSPORT = re.compile(
    r"(?:паспорт\w*|сери[яи])\D{0,40}?(\d{2}\s?\d{2})\D{0,25}?(\d{3}\s?\d{3})(?!\d)", re.IGNORECASE
)


def passport_hits(text: str) -> list[Hit]:
    out = []
    for m in _PASSPORT.finditer(text):
        between = text[m.end(1) : m.start(2)]
        if re.search(r"\d", between) or len(between) > 25:
            continue
        out.append(Hit(m.start(1), m.end(2), PiiType.PASSPORT, 1.0))
    return out


# ---------------------------------------------------------------- email

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_EMAIL_SPOKEN = re.compile(
    r"\b[a-z0-9][a-z0-9._-]*\s*(?:собака|собачка)\s*[a-z0-9-]+(?:\s*(?:\.|точка)\s*[a-z0-9-]+)*"
    r"\s*(?:\.|точка)\s*[a-z]{2,6}\b",
    re.IGNORECASE,
)


# ASR склеивает продиктованный адрес в одно слово: «иван.ptrovsobakamail.ru».
_EMAIL_GLUED = re.compile(r"\S*(?:собака|собачка|sobaka)\s?\S*\.(?:ru|com|net|org|рф|su|io|me|info|biz)\b", re.IGNORECASE)
_EMAIL_CTX = re.compile(r"(?:почт\w*|e-?mail|имейл\w*|емейл\w*|мейл\w*|ящик\w*)\W{1,20}?"
                        r"(\S+\.(?:ru|com|net|org|рф|su|io|me|info|biz))\b", re.IGNORECASE)


def email_hits(text: str) -> list[Hit]:
    out = [Hit(m.start(), m.end(), PiiType.EMAIL) for m in _EMAIL.finditer(text)]
    out += [Hit(m.start(), m.end(), PiiType.EMAIL, 0.9) for m in _EMAIL_SPOKEN.finditer(text)]
    out += [Hit(m.start(), m.end(), PiiType.EMAIL, 0.7) for m in _EMAIL_GLUED.finditer(text)]
    out += [Hit(m.start(1), m.end(1), PiiType.EMAIL, 0.7) for m in _EMAIL_CTX.finditer(text)]
    return out


# ---------------------------------------------------------------- дата рождения

_MONTHS = r"(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)[а-я]*"
_MONTH_NUM = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8,
    "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}
_DATE = re.compile(
    rf"(?P<d>\d{{1,2}})\s*(?:[./-]\s*(?P<m>\d{{1,2}})\s*[./-]\s*(?P<y>\d{{2,4}})|\s+(?P<mn>{_MONTHS})"
    rf"(?:\s+(?P<y2>\d{{2,4}})(?:\s*(?:года|год|г\.?))?)?)",
    re.IGNORECASE,
)
_YEAR_BIRTH = re.compile(r"(?P<y>\d{4})\s+(?:года?|г\.?)\s+рождения", re.IGNORECASE)
_BIRTH_CTX = re.compile(r"родил\w*|рождени\w*|д\.\s?р\.|др\b", re.IGNORECASE)


def _date_norm(m: re.Match) -> str:
    d = int(m.group("d"))
    if m.group("mn"):
        stem = next((k for k in _MONTH_NUM if m.group("mn").lower().startswith(k)), None)
        mo = _MONTH_NUM.get(stem, 0)
        y = m.group("y2")
    else:
        mo = int(m.group("m"))
        y = m.group("y")
    if y and len(y) == 2:
        y = ("19" if int(y) > 30 else "20") + y
    return f"{d:02d}.{mo:02d}.{y or '????'}"


def birthdate_hits(text: str) -> list[Hit]:
    out = []
    for ctx in _BIRTH_CTX.finditer(text):
        window_end = min(len(text), ctx.end() + 40)
        m = _DATE.search(text, ctx.end(), window_end)
        if m:
            out.append(Hit(m.start(), m.end(), PiiType.BIRTHDATE, 1.0, norm=_date_norm(m)))
            continue
        before = text[max(0, ctx.start() - 40) : ctx.start()]
        found = list(_DATE.finditer(before))
        mb = found[-1] if found else None
        if mb:
            off = max(0, ctx.start() - 40)
            out.append(Hit(off + mb.start(), off + mb.end(), PiiType.BIRTHDATE, 0.9, norm=_date_norm(mb)))
    for m in _YEAR_BIRTH.finditer(text):
        out.append(Hit(m.start("y"), m.end("y"), PiiType.BIRTHDATE, 0.9, norm=m.group("y")))
    return out


# ---------------------------------------------------------------- коды и кодовые слова

_CODE_CTX = re.compile(
    r"код\w*(?:\s+\w+){0,2}?\s+(?:из|в)\s+(?:смс|sms|сообщени\w*|пуш\w*)|код\w* подтверждени\w*|"
    r"одноразов\w+ код\w*|пин-?код\w*|\bcvv\b|\bcvc\b|кодов\w+ слов\w*|секретн\w+ слов\w*|"
    r"контрольн\w+ слов\w*|кодов\w+ фраз\w*",
    re.IGNORECASE,
)
_CODE_DIGITS = re.compile(r"\d(?:[ -]?\d){2,7}(?!\d)")
_CODE_WORD = re.compile(r"[«\"„“']([^»\"“”']{1,40})[»\"“”']|([A-Za-zА-Яа-яЁё][\w\-]*)")
_CODE_SKIP = {
    "это", "у", "меня", "будет", "такое", "такой", "моё", "мое", "мой", "слово", "ну", "вот", "значит",
    "у вас", "пожалуйста", "скажите", "назовите", "его", "её", "ее", "какое", "какой", "был", "было",
    "есть", "там", "вам", "вас", "нас", "мы", "я", "же", "а", "и", "да", "так", "сейчас", "пришёл",
    "пришел", "придёт", "придет", "получили", "получил", "продиктуйте", "называется", "звучит",
}


def code_hits(text: str) -> list[Hit]:
    out = []
    for c in _CODE_CTX.finditer(text):
        tail_start = c.end()
        window = text[tail_start : tail_start + 40]
        is_word = "слов" in c.group().lower() or "фраз" in c.group().lower()
        if not is_word:
            m = _CODE_DIGITS.search(window)
            if m and not re.search(r"[.!?]", window[: m.start()]):
                out.append(Hit(tail_start + m.start(), tail_start + m.end(), PiiType.CODE))
            continue
        for m in _CODE_WORD.finditer(window):
            if re.search(r"[.!?]", window[: m.start()]):
                break
            if m.group(1):
                out.append(Hit(tail_start + m.start(1), tail_start + m.end(1), PiiType.CODE))
                break
            w = m.group(2)
            if w.lower() in _CODE_SKIP:
                continue
            out.append(Hit(tail_start + m.start(2), tail_start + m.end(2), PiiType.CODE))
            break
    return out


# ---------------------------------------------------------------- адреса

_STREET = (
    r"(?:улиц[аеыу]|ул\.|проспект[еау]?|пр-т|просп\.|переул(?:ок|ке|ка)|пер\.|бульвар[еау]?|б-р|"
    r"шоссе|площад[ьи]|набережн(?:ая|ой|ую)|наб\.|проезд[еау]?|микрорайон[еау]?|мкр\.?|тупик[еау]?|"
    r"алле[яеи])"
)
_PROPER = r"(?:\d{1,2}[\s-]+)?[А-ЯЁ][А-Яа-яЁё\-]*\.?(?:\s+[А-ЯЁ][А-Яа-яЁё\-]*){0,2}"
_HOUSE = r"(?:,?\s*(?:дом|д\.)\s*(?:номер\s*)?\d+\s?[а-яА-Я]?(?:/\d+)?)"
_BLOCK = r"(?:,?\s*(?:корпус|корп\.|к\.|строение|стр\.)\s*\d+[а-я]?)"
_FLAT = r"(?:,?\s*(?:квартира|квартиры|квартире|кв\.|офис|офиса)\s*(?:номер\s*)?\d+)"
_ADDR_STREET_FIRST = re.compile(
    rf"(?i:{_STREET})\s+{_PROPER}(?:,?\s*\d+[а-я]?(?=[\s,.]|$))?{_HOUSE}?{_BLOCK}?{_FLAT}?"
)
_ADDR_NAME_FIRST = re.compile(
    rf"[А-ЯЁ][а-яё]+(?:ой|ая|ую|ий|ый|ом|ей)\s+(?i:{_STREET}){_HOUSE}?{_BLOCK}?{_FLAT}?"
)
_ADDR_HOUSE_ONLY = re.compile(rf"(?i:(?:дом|д\.)\s*(?:номер\s*)?\d+\s?[а-я]?(?:/\d+)?){_BLOCK}?{_FLAT}")


def address_hits(text: str) -> list[Hit]:
    out = []
    for rx, conf in ((_ADDR_STREET_FIRST, 0.9), (_ADDR_NAME_FIRST, 0.9), (_ADDR_HOUSE_ONLY, 0.8)):
        for m in rx.finditer(text):
            end = m.end()
            while end > m.start() and text[end - 1] in " ,.":
                end -= 1
            out.append(Hit(m.start(), end, PiiType.ADDRESS, conf))
    return out


# ---------------------------------------------------------------- организации по ОПФ

_OPF = r"(?:ООО|ОАО|ЗАО|ПАО|НАО|АО|НКО|ФГУП|ГУП|МУП|ТОО|АНО|ФГБУ|ГБУ|МБУ)"
_ORG_QUOTED = re.compile(rf"\b{_OPF}\s*(?:[«\"„“]([^»\"“”]{{1,60}})[»\"“”])")
_ORG_IP = re.compile(r"\bИП\s+[А-ЯЁ][а-яё\-]+(?:\s+[А-ЯЁ][а-яё\-]*\.?){0,2}")
_ORG_KIND_QUOTED = re.compile(
    r"(?i:компани\w*|фирм\w*|банк\w*|магазин\w*|организаци\w*|предприяти\w*|завод\w*|холдинг\w*|"
    r"агентств\w*|клиник\w*|центр\w*)\s+([«\"„“][^»\"“”]{1,60}[»\"“”])"
)


_ORG_KIND_CAP = re.compile(
    r"(?i:компани[яиюей]|фирм[аеуыой]|организаци[яиюей]|предприяти[еяю]|магазин[аеу]?|банк[аеу]?|"
    r"агентств[оау]|клиник[аеуи]|холдинг[аеу]?|завод[аеу]?|салон[аеу]?|студи[яиюей])\s+"
    r"((?:[«\"„“])?[A-ZА-ЯЁ][\w\-]+(?:\s+[A-ZА-ЯЁ][\w\-]+){0,2})"
)


def org_hits(text: str) -> list[Hit]:
    out = []
    for m in _ORG_KIND_CAP.finditer(text):
        if m.group(1)[0] in "«\"„“":
            continue
        out.append(Hit(m.start(1), m.end(1), PiiType.ORG, 0.7))
    for m in _ORG_QUOTED.finditer(text):
        out.append(Hit(m.start(), m.end(), PiiType.ORG, 1.0, norm=m.group(1).strip().lower(), declinable=False))
    for m in _ORG_IP.finditer(text):
        out.append(Hit(m.start(), m.end(), PiiType.ORG, 0.9, norm=m.group().lower(), declinable=False))
    for m in _ORG_KIND_QUOTED.finditer(text):
        inner = m.group(1).strip("«»\"„“”").strip().lower()
        out.append(Hit(m.start(1), m.end(1), PiiType.ORG, 0.9, norm=inner, declinable=False))
    return out


ALL_RULES = [number_hits, passport_hits, email_hits, birthdate_hits, code_hits, address_hits, org_hits]
