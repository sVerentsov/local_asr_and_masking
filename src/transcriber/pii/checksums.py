"""Контрольные суммы для форматных ПД."""

from __future__ import annotations


def luhn_ok(digits: str) -> bool:
    if not digits.isdigit() or len(digits) < 12:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def snils_ok(digits: str) -> bool:
    if len(digits) != 11 or not digits.isdigit():
        return False
    body, control = digits[:9], int(digits[9:])
    if int(body) <= 1001998:
        return False
    s = sum(int(d) * w for d, w in zip(body, range(9, 0, -1)))
    if s < 100:
        expect = s
    elif s in (100, 101):
        expect = 0
    else:
        expect = s % 101
        if expect == 100:
            expect = 0
    return control == expect


def _inn_digit(digits: str, weights: list[int]) -> int:
    return sum(int(d) * w for d, w in zip(digits, weights)) % 11 % 10


def inn_ok(digits: str) -> bool:
    if not digits.isdigit():
        return False
    if len(digits) == 10:
        return _inn_digit(digits, [2, 4, 10, 3, 5, 9, 4, 6, 8]) == int(digits[9])
    if len(digits) == 12:
        n11 = _inn_digit(digits, [7, 2, 4, 10, 3, 5, 9, 4, 6, 8])
        n12 = _inn_digit(digits, [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8])
        return n11 == int(digits[10]) and n12 == int(digits[11])
    return False
