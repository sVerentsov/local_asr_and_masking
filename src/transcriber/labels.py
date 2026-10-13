"""Формат меток и масок. Общий для маскирования, защиты от повторного маскирования и парсера ответов."""

from __future__ import annotations

import re

from transcriber.model import PiiType

PHONE_MASK_PREFIX_DIGITS = "7000000"
EMAIL_MASK_DOMAIN = "example.com"
MAX_PHONE_MASKS = 9999

BRACKET_TYPES = [t.value for t in PiiType if t not in (PiiType.PHONE, PiiType.EMAIL)]


def phone_mask(n: int) -> str:
    if not 1 <= n <= MAX_PHONE_MASKS:
        raise ValueError("В деле слишком много телефонов для маски")
    return f"+7 000 000-{n // 100:02d}-{n % 100:02d}"


def email_mask(n: int) -> str:
    return f"user{n}@{EMAIL_MASK_DOMAIN}"


def bracket_label(type_: str, n: int, gcase: str | None = None) -> str:
    return f"[{type_}_{n}:{gcase}]" if gcase else f"[{type_}_{n}]"


def phone_mask_number(digits: str) -> int | None:
    """Номер маски по цифрам телефона, если это наша маска (+7 000 000-NN-NN или 8 000 000-NN-NN)."""
    if len(digits) == 11 and digits[0] in "78":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    if len(digits) == 11 and digits.startswith(PHONE_MASK_PREFIX_DIGITS):
        n = int(digits[7:])
        return n or None
    return None


# Метки в нашем собственном выводе. Используется для защиты от повторного маскирования.
_TYPES_RE = "|".join(re.escape(t) for t in sorted(BRACKET_TYPES, key=len, reverse=True))
OWN_LABEL_RE = re.compile(rf"\[(?:{_TYPES_RE})_\d+(?::[а-я]+)?\]|\[…\]")
OWN_PHONE_RE = re.compile(r"\+7 000 000-\d\d-\d\d")
OWN_EMAIL_RE = re.compile(rf"user\d+@{re.escape(EMAIL_MASK_DOMAIN)}", re.IGNORECASE)


def protected_ranges(text: str) -> list[tuple[int, int]]:
    out = []
    for rx in (OWN_LABEL_RE, OWN_PHONE_RE, OWN_EMAIL_RE):
        out.extend(m.span() for m in rx.finditer(text))
    return out
