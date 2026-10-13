"""Словарь соответствий дела: сущности, сквозная нумерация меток, объединение."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from transcriber.labels import bracket_label, email_mask, phone_mask
from transcriber.model import Entity, PiiType, Span, new_id

DIGIT_TYPES = {
    PiiType.CARD.value,
    PiiType.ACCOUNT.value,
    PiiType.SNILS.value,
    PiiType.INN.value,
    PiiType.PASSPORT.value,
}


def digits_of(s: str) -> str:
    return re.sub(r"\D", "", s)


def normalize_phone(s: str) -> str:
    d = digits_of(s)
    if len(d) == 11 and d[0] == "8":
        d = "7" + d[1:]
    elif len(d) == 10:
        d = "7" + d
    return d


def entity_key(span: Span) -> str:
    t = span.type
    if t == PiiType.PHONE:
        return normalize_phone(span.value)
    if t in DIGIT_TYPES:
        return digits_of(span.value)
    if t in (PiiType.PER, PiiType.ORG, PiiType.BIRTHDATE) and span.norm:
        return span.norm.lower()
    return re.sub(r"\s+", " ", span.value.strip().lower().replace("ё", "е"))


def canonical_value(span: Span) -> str:
    if span.type == PiiType.PER and span.norm:
        return " ".join(_cap(tok) for tok in span.norm.split())
    if span.type == PiiType.ORG and span.norm and span.declinable:
        return _restore_caps(span.norm, span.value)
    return span.value


def _cap(word: str) -> str:
    return "-".join(p[:1].upper() + p[1:] for p in word.split("-"))


def _restore_caps(norm: str, surface: str) -> str:
    """Нормальная форма названия с регистром букв как в исходном упоминании."""
    out = []
    surf = surface.split()
    for i, w in enumerate(norm.split()):
        src = surf[i] if i < len(surf) else ""
        if src.isupper() and len(src) > 1:
            out.append(w.upper())
        elif src[:1].isupper():
            out.append(_cap(w))
        else:
            out.append(w)
    return " ".join(out)


@dataclass
class Vault:
    code: str
    entities: dict[str, Entity] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)

    # --- сериализация ---
    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "entities": [e.to_dict() for e in self.entities.values()],
            "counters": self.counters,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Vault:
        ents = {e["id"]: Entity.from_dict(e) for e in d.get("entities", [])}
        return cls(code=d["code"], entities=ents, counters=dict(d.get("counters", {})))

    # --- поиск ---
    def get(self, entity_id: str | None) -> Entity | None:
        seen = set()
        e = self.entities.get(entity_id) if entity_id else None
        while e is not None and e.alias_of and e.id not in seen:
            seen.add(e.id)
            e = self.entities.get(e.alias_of)
        return e

    def by_label(self, type_: str, n: int) -> Entity | None:
        for e in self.entities.values():
            if e.type == type_ and e.n == n:
                return self.get(e.id)
        return None

    def live(self) -> list[Entity]:
        return [e for e in self.entities.values() if not e.alias_of]

    # --- метки ---
    def label(self, entity: Entity, gcase: str | None = None) -> str:
        if entity.type == PiiType.PHONE:
            return phone_mask(entity.n)
        if entity.type == PiiType.EMAIL:
            return email_mask(entity.n)
        if entity.type in (PiiType.PER, PiiType.ORG) and entity.declinable:
            return bracket_label(entity.type, entity.n, gcase or "им")
        return bracket_label(entity.type, entity.n)

    # --- сопоставление находки и сущности ---
    def match_or_create(self, span: Span) -> Entity:
        key = entity_key(span)
        if span.type == PiiType.PER:
            ent = self._match_person(key)
        else:
            ent = next((e for e in self.live() if e.type == span.type and e.key == key), None)
        if ent is None:
            ent = self._create(span, key)
        else:
            self._absorb(ent, span, key)
        span.entity_id = ent.id
        return ent

    def _match_person(self, key: str) -> Entity | None:
        toks = set(key.split())
        if not toks:
            return None
        cands = []
        for e in self.live():
            if e.type != PiiType.PER:
                continue
            et = set(e.key.split())
            if et == toks:
                return e
            if toks < et or et < toks:
                cands.append(e)
        return cands[0] if len(cands) == 1 else None

    def _create(self, span: Span, key: str) -> Entity:
        n = self.counters.get(span.type, 0) + 1
        self.counters[span.type] = n
        ent = Entity(
            id=new_id(),
            type=span.type,
            n=n,
            value=canonical_value(span),
            key=key,
            gender=span.gender,
            declinable=span.declinable if span.type in (PiiType.PER, PiiType.ORG) else False,
            variants=[span.value],
        )
        self.entities[ent.id] = ent
        return ent

    def _absorb(self, ent: Entity, span: Span, key: str) -> None:
        if span.value not in ent.variants:
            ent.variants.append(span.value)
        if ent.gender is None and span.gender:
            ent.gender = span.gender
        if ent.type == PiiType.PER and len(key.split()) > len(ent.key.split()):
            ent.key = key
            ent.value = canonical_value(span)

    def merge(self, keep_id: str, drop_id: str) -> Entity:
        keep, drop = self.get(keep_id), self.get(drop_id)
        if keep is None or drop is None or keep.id == drop.id:
            raise ValueError("Нельзя объединить эти сущности")
        if keep.type != drop.type:
            raise ValueError("Объединять можно только сущности одного типа")
        for v in drop.variants:
            if v not in keep.variants:
                keep.variants.append(v)
        drop.alias_of = keep.id
        return keep

    def add_manual(self, type_: str, value: str, *, norm: str | None = None, gender: str | None = None,
                   declinable: bool = True) -> Entity:
        span = Span(type=type_, parts=[], value=value, source="user", norm=norm, gender=gender,
                    declinable=declinable)
        return self.match_or_create(span)


def new_vault(code: str) -> Vault:
    return Vault(code=code)


__all__ = ["Vault", "new_vault", "entity_key", "digits_of", "normalize_phone", "new_id"]
