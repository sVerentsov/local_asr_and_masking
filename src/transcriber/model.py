"""Модель данных: реплики, находки ПД, сущности дела и словарь соответствий."""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from enum import StrEnum


class PiiType(StrEnum):
    PER = "ФИО"
    ORG = "ОРГ"
    PHONE = "ТЕЛЕФОН"
    EMAIL = "EMAIL"
    CARD = "КАРТА"
    ACCOUNT = "СЧЁТ"
    PASSPORT = "ПАСПОРТ"
    SNILS = "СНИЛС"
    INN = "ИНН"
    ADDRESS = "АДРЕС"
    BIRTHDATE = "ДР"
    CODE = "КОД"


TYPE_TITLES = {
    PiiType.PER: "ФИО",
    PiiType.ORG: "Организация",
    PiiType.PHONE: "Телефон",
    PiiType.EMAIL: "Email",
    PiiType.CARD: "Номер карты",
    PiiType.ACCOUNT: "Расчётный счёт",
    PiiType.PASSPORT: "Паспорт",
    PiiType.SNILS: "СНИЛС",
    PiiType.INN: "ИНН",
    PiiType.ADDRESS: "Адрес",
    PiiType.BIRTHDATE: "Дата рождения",
    PiiType.CODE: "Код или кодовое слово",
}

ALL_TYPES = [t.value for t in PiiType]

# Падежи в метках и соответствие граммемам pymorphy3 и UD (slovnet).
CASES = ["им", "род", "дат", "вин", "тв", "пр"]
CASE_TO_PYMORPHY = {"им": "nomn", "род": "gent", "дат": "datv", "вин": "accs", "тв": "ablt", "пр": "loct"}
UD_TO_CASE = {"Nom": "им", "Gen": "род", "Dat": "дат", "Acc": "вин", "Ins": "тв", "Loc": "пр"}

DECLINABLE_TYPES = {PiiType.PER.value, PiiType.ORG.value}


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def new_case_code() -> str:
    return f"DEL-{secrets.token_hex(2).upper()}"


@dataclass
class Utterance:
    text: str
    speaker: str | None = None
    start: float | None = None
    end: float | None = None


@dataclass
class Part:
    """Фрагмент находки внутри одной реплики. field — текст реплики или имя спикера."""

    utt: int
    start: int
    end: int
    field: str = "text"


@dataclass
class Span:
    type: str
    parts: list[Part]
    value: str
    source: str = "rule"  # rule | ner | dict | user
    confidence: float = 1.0
    gcase: str | None = None
    gender: str | None = None  # masc | femn | None
    norm: str | None = None  # нормальная форма или ключ сопоставления
    case_flag: bool = False  # падеж не определён, показать на проверке
    declinable: bool = True
    entity_id: str | None = None
    id: str = field(default_factory=new_id)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Span:
        d = dict(d)
        d["parts"] = [Part(**p) for p in d["parts"]]
        return cls(**_known(cls, d))


@dataclass
class Entity:
    id: str
    type: str
    n: int
    value: str  # каноническая форма: им. падеж для ФИО и организаций, исходник для остальных
    key: str
    gender: str | None = None
    declinable: bool = True
    variants: list[str] = field(default_factory=list)
    alias_of: str | None = None  # после объединения метка продолжает работать

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Entity:
        return cls(**_known(cls, d))


@dataclass
class Transcript:
    id: str
    title: str
    utterances: list[Utterance]
    spans: list[Span] = field(default_factory=list)
    speakers: dict[str, str] = field(default_factory=dict)  # исходное имя → отображаемое
    reviewed: bool = False
    created: float = field(default_factory=time.time)
    duration: float | None = None
    source_kind: str = "audio"  # audio | text
    dismissed: list[str] = field(default_factory=list)  # сущности, с которых маску сняли вручную

    def speaker_name(self, raw: str | None) -> str | None:
        if raw is None:
            return None
        return self.speakers.get(raw, raw)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Transcript:
        d = dict(d)
        d["utterances"] = [Utterance(**u) for u in d["utterances"]]
        d["spans"] = [Span.from_dict(s) for s in d.get("spans", [])]
        return cls(**_known(cls, d))


def _known(cls, d: dict) -> dict:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in d.items() if k in names}
