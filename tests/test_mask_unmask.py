import pytest

from tests.conftest import SECRETS
from transcriber.mask import detect_and_assign, masked_lines, normalize_transcript
from transcriber.model import PiiType, Transcript, Utterance
from transcriber.unmask.inflect import inflect_org, inflect_person
from transcriber.unmask.parser import find_case_code, find_labels
from transcriber.unmask.restore import UNKNOWN, infer_case, restore
from transcriber.vault import Vault


@pytest.fixture
def masked(call_transcript):
    vault = Vault(code="DEL-AB12")
    normalize_transcript(call_transcript)
    detect_and_assign(call_transcript, vault)
    return call_transcript, vault


def test_no_secrets_after_masking(masked):
    tr, vault = masked
    text = "\n".join(masked_lines(tr, vault))
    for s in SECRETS:
        assert s.lower() not in text.lower(), s


def test_phone_across_interruption(masked):
    tr, _ = masked
    phone = next(s for s in tr.spans if s.type == PiiType.PHONE)
    assert [p.utt for p in phone.parts] == [1, 3]
    lines = masked_lines(tr, Vault.from_dict(masked[1].to_dict()))
    assert "+7 000 000-00-01" in lines[1]
    assert "[…]" in lines[3]


def test_same_person_same_label_with_case(masked):
    tr, vault = masked
    per = [s for s in tr.spans if s.type == PiiType.PER and "Петров" in s.value]
    assert len({s.entity_id for s in per}) == 1
    cases = {s.value: s.gcase for s in per}
    assert cases["Петров Иван Сергеевич"] == "им"
    assert cases["Петрову Ивану"] == "дат"
    ent = vault.get(per[0].entity_id)
    assert ent.value == "Петров Иван Сергеевич"
    assert ent.gender == "masc"


def test_quoted_org_has_no_case(masked):
    tr, vault = masked
    org = next(s for s in tr.spans if s.value == "ООО «Ромашка»")
    assert vault.label(vault.get(org.entity_id)) == "[ОРГ_1]"


def test_idempotent(masked):
    tr, vault = masked
    again = Transcript(id="2", title="", utterances=[Utterance(line.split(": ", 1)[1], "X")
                                                     for line in masked_lines(tr, vault)])
    detect_and_assign(again, Vault(code="DEL-0000"))
    assert again.spans == []


def test_numbering_shared_across_transcripts(masked):
    tr, vault = masked
    other = Transcript(id="2", title="", utterances=[Utterance("Позвоните Петрову Ивану Сергеевичу.", "A"),
                                                     Utterance("Ещё звонил Сидоров Пётр.", "A")])
    detect_and_assign(other, vault)
    labels = masked_lines(other, vault)
    petrov = next(e for e in vault.live() if e.value == "Петров Иван Сергеевич")
    assert f"[ФИО_{petrov.n}:дат]" in labels[0]
    assert f"[ФИО_{max(e.n for e in vault.live() if e.type == PiiType.PER)}:им]" in labels[1]


def test_roundtrip_with_cases(masked):
    _, vault = masked
    petrov = next(e for e in vault.live() if e.value == "Петров Иван Сергеевич")
    zaitseva = next(e for e in vault.live() if e.value == "Мария Зайцева")
    answer = (f"Клиент [ФИО_{petrov.n}:им] просит перезвонить. Передайте [ФИО_{petrov.n}:дат] документы, "
              f"а с [ФИО_{zaitseva.n}:тв] свяжитесь позже. Телефон: +7 (000) 000-00-01.")
    res = restore(answer, vault)
    assert res.text == ("Клиент Петров Иван Сергеевич просит перезвонить. Передайте Петрову Ивану Сергеевичу "
                        "документы, а с Марией Зайцевой свяжитесь позже. Телефон: 8 916 123 45 67.")
    assert res.unknown == 0


def test_unknown_label(masked):
    _, vault = masked
    res = restore("Упомянут [ФИО_99:им] и [КАРТА_7].", vault)
    assert res.text == f"Упомянут {UNKNOWN} и {UNKNOWN}."
    assert res.unknown == 2
    assert [h.kind for h in res.highlights] == ["unknown", "unknown"]


def test_markdown_preserved(masked):
    _, vault = masked
    res = restore("**[ФИО_2:им]** и [**ФИО_2:дат**]\n- \\[ФИО\\_2\\]", vault)
    assert res.text.startswith("**Петров Иван Сергеевич** и **Петрову Ивану Сергеевичу**\n- ")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("[ФИО_1:дат]", ("ФИО", 1, "дат")),
        ("[ФИО_1]", ("ФИО", 1, None)),
        ("ФИО_1", ("ФИО", 1, None)),
        ("[ФИО 1]", ("ФИО", 1, None)),
        ("\\[ФИО\\_1\\]", ("ФИО", 1, None)),
        ("[ФИО_1: Дат.]", ("ФИО", 1, "дат")),
        ("[СЧЕТ_3]", ("СЧЁТ", 3, None)),
        ("+7 (000) 000-01-02", ("ТЕЛЕФОН", 102, None)),
        ("8-000-000-00-05", ("ТЕЛЕФОН", 5, None)),
        ("user12@example.com", ("EMAIL", 12, None)),
    ],
)
def test_parser_variants(text, expected):
    found = find_labels(f"до {text} после")
    assert [(f.type, f.n, f.gcase) for f in found] == [expected]


def test_parser_ignores_plain_words():
    assert find_labels("укажите фио 2 раза, код 5") == []


def test_case_code():
    assert find_case_code("Ответ по делу DEL-7F3A: всё хорошо") == "DEL-7F3A"


@pytest.mark.parametrize(
    "value,gcase,expected",
    [
        ("Петров Иван Сергеевич", "дат", "Петрову Ивану Сергеевичу"),
        ("Петрова Анна Ивановна", "вин", "Петрову Анну Ивановну"),
        ("Шевченко Олег", "род", "Шевченко Олега"),
        ("Ким Анна", "тв", "Ким Анной"),
        ("Достоевский Фёдор", "пр", "Достоевском Фёдоре"),
        ("Римский-Корсаков Николай", "дат", "Римскому-Корсакову Николаю"),
        ("Белых Ольга", "род", "Белых Ольги"),
    ],
)
def test_inflect_person(value, gcase, expected):
    assert inflect_person(value, gcase, None) == expected


def test_inflect_org():
    assert inflect_org("Сбербанк", "пр") == "Сбербанке"
    assert inflect_org("Банк Москвы", "дат") == "Банку Москвы"


@pytest.mark.parametrize(
    "before,expected",
    [
        ("Позвоните ", "дат"),
        ("Свяжитесь с ", "тв"),
        ("Документы для ", "род"),
        ("Договорились о ", "пр"),
        ("Итог.\n", "им"),
        ("Выслать договор ", None),
    ],
)
def test_infer_case(before, expected):
    assert infer_case(before + "X", len(before)) == expected


def test_speaker_names_masked():
    tr = Transcript(id="s", title="", utterances=[Utterance("Добрый день.", "Иван Петров"),
                                                  Utterance("Здравствуйте.", "Оператор")])
    vault = Vault(code="DEL-0001")
    detect_and_assign(tr, vault)
    lines = masked_lines(tr, vault)
    assert lines[0].startswith("[ФИО_1:им]:")
    assert lines[1].startswith("Оператор:")


def test_disabled_types_not_masked(call_transcript):
    from transcriber.pii.detect import DetectSettings

    vault = Vault(code="DEL-0002")
    normalize_transcript(call_transcript)
    detect_and_assign(call_transcript, vault, DetectSettings(types=[PiiType.PHONE.value]))
    assert {s.type for s in call_transcript.spans} == {PiiType.PHONE.value}


def test_common_words_not_masked():
    tr = Transcript(id="fp", title="", utterances=[
        Utterance("Скидка 15 процентов до 31 декабря, сумма 125 000 рублей.", "A"),
        Utterance("Звонок по поводу заказа номер 4512 от 12 марта.", "A"),
        Utterance("Вера в успех — главное. Интеграция через API.", "A"),
    ])
    detect_and_assign(tr, Vault(code="DEL-FP00"))
    assert tr.spans == []
