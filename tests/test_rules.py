import pytest

from transcriber.model import PiiType
from transcriber.normalize import normalize_numbers
from transcriber.pii.checksums import inn_ok, luhn_ok, snils_ok
from transcriber.pii.rules import (
    address_hits,
    birthdate_hits,
    code_hits,
    email_hits,
    number_hits,
    org_hits,
    passport_hits,
)


def types(hits, text):
    return [(h.type, text[h.start : h.end]) for h in hits]


def test_checksums():
    assert luhn_ok("4111111111111111")
    assert not luhn_ok("4111111111111112")
    assert snils_ok("11223344595")
    assert not snils_ok("11223344596")
    assert inn_ok("7707083893")
    assert inn_ok("500100732259")
    assert not inn_ok("7707083894")


@pytest.mark.parametrize(
    "src,expected",
    [
        ("мой номер восемь девятьсот шестнадцать сто двадцать три сорок пять шестьдесят семь",
         "мой номер 8 916 123 45 67"),
        ("родился пятнадцатого марта тысяча девятьсот восемьдесят пятого года", "родился 15 марта 1985 года"),
        ("у меня два вопроса и один ответ", "у меня 2 вопроса и один ответ"),
        ("второй вопрос", "второй вопрос"),
        ("код ноль ноль семь", "код 0 0 7"),
        ("пять тысяч рублей", "5000 рублей"),
        ("серия сорок пять двенадцать", "серия 45 12"),
    ],
)
def test_normalize_numbers(src, expected):
    assert normalize_numbers(src) == expected


@pytest.mark.parametrize(
    "text,typ",
    [
        ("звоните +7 (916) 123-45-67", PiiType.PHONE),
        ("номер 8 916 123 45 67", PiiType.PHONE),
        ("мобильный 9161234567", PiiType.PHONE),
        ("карта 4111 1111 1111 1111", PiiType.CARD),
        ("счёт 40817810099910004312", PiiType.ACCOUNT),
        ("СНИЛС 112-233-445 95", PiiType.SNILS),
        ("ИНН 7707083893", PiiType.INN),
        ("ИНН 500100732259", PiiType.INN),
    ],
)
def test_number_types(text, typ):
    hits = number_hits(text)
    assert [h.type for h in hits] == [typ]


def test_number_run_split_into_two():
    text = "телефон 8 916 123 45 67 и карта 4111 1111 1111 1111"
    assert [h.type for h in number_hits(text)] == [PiiType.PHONE, PiiType.CARD]


def test_short_numbers_ignored():
    assert number_hits("заказ 12345 на 2 штуки за 1500 рублей в 2024 году") == []


def test_passport():
    text = "паспорт серия 45 12 номер 345678"
    assert types(passport_hits(text), text) == [(PiiType.PASSPORT, "45 12 номер 345678")]


def test_email_variants():
    text = "почта ivan.petrov@mail.ru или ivan собака yandex точка ru, ещё иван.ptrovsobakamail.ru"
    found = [v for _, v in types(email_hits(text), text)]
    assert "ivan.petrov@mail.ru" in found
    assert any("yandex" in v for v in found)
    assert any("sobaka" in v for v in found)


def test_birthdate():
    text = "я родился 15 марта 1985 года, дата рождения 03.04.1990"
    hits = birthdate_hits(text)
    assert {h.norm for h in hits} == {"15.03.1985", "03.04.1990"}


def test_codes():
    text = "код из смс 4512. Кодовое слово «Барсик». Кодовое слово у меня будет ромашка"
    assert [v for _, v in types(code_hits(text), text)] == ["4512", "Барсик", "ромашка"]


def test_address():
    text = "живу на улице Ленина, дом 5, квартира 12, а офис на Тверской улице"
    vals = [v for _, v in types(address_hits(text), text)]
    assert "улице Ленина, дом 5, квартира 12" in vals
    assert "Тверской улице" in vals


def test_org_rules():
    text = "из ООО «Ромашка» и ИП Сидоров, компания Вектор"
    hits = org_hits(text)
    vals = [text[h.start : h.end] for h in hits]
    assert "ООО «Ромашка»" in vals
    assert "ИП Сидоров" in vals
    assert "Вектор" in vals
    assert next(h for h in hits if text[h.start : h.end] == "ООО «Ромашка»").declinable is False
