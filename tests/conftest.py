import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Модели для медленных тестов можно взять из каталога разработчика.
if "TRANSCRIBER_MODELS" not in os.environ and (ROOT / ".devdata" / "models").exists():
    os.environ["TRANSCRIBER_MODELS"] = str(ROOT / ".devdata" / "models")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSCRIBER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRANSCRIBER_DOWNLOADS", str(tmp_path / "downloads"))
    return tmp_path


CALL = [
    ("Оператор", "Здравствуйте, меня зовут Анна, компания ООО «Ромашка». С кем я говорю?"),
    ("Клиент", "Петров Иван Сергеевич. Мой номер восемь девятьсот шестнадцать"),
    ("Оператор", "угу"),
    ("Клиент", "сто двадцать три сорок пять шестьдесят семь"),
    ("Оператор", "Перезвоните Петрову Ивану завтра. Почта ivan.petrov@mail.ru, карта 4276 3800 1234 5678"),
    ("Клиент", "Паспорт серия 45 12 номер 345678, родился 15 марта 1985 года, живу на улице Ленина, дом 5, "
               "квартира 12. Кодовое слово «Барсик». СНИЛС 112-233-445 95, ИНН 7707083893"),
    ("Клиент", "Я работаю в Сбербанке, а жена Мария Зайцева в Газпроме. Код из смс 4512."),
]

SECRETS = ["Петров", "Иван", "Анна", "Зайцев", "Мария", "916", "123 45 67", "ivan.petrov", "4276", "345678",
           "45 12", "Ленина", "Барсик", "112-233-445", "7707083893", "Сбербанк", "Газпром", "Ромашка", "4512",
           "1985"]


@pytest.fixture
def call_transcript():
    from transcriber.model import Transcript, Utterance

    return Transcript(id="t1", title="call", utterances=[Utterance(t, s) for s, t in CALL])
