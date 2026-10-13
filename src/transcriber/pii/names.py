"""Имена и организации: NER Natasha, словарный добор по pymorphy3, морфология для падежа и нормальной формы."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from transcriber.model import UD_TO_CASE, PiiType
from transcriber.nlp import Token, is_known, morph_tag, ner_spans, parse, tokenize

_NAME_TAGS = ("Name", "Surn", "Patr")
_SKIP_POS_EDGE = {"VERB", "AUX", "ADP", "CCONJ", "SCONJ", "PRON", "DET", "PART", "PUNCT", "ADV", "INTJ", "NUM"}
_ROLE_WORDS = {
    "оператор", "клиент", "менеджер", "спикер", "собеседник", "агент", "абонент", "интервьюер",
    "респондент", "продавец", "покупатель", "консультант", "специалист", "сотрудник", "поддержка",
}
# Аббревиатуры, которые NER принимает за организации, но они ничего не говорят о собеседнике.
_ORG_STOP = {
    "API", "CRM", "ERP", "SMS", "СМС", "IT", "HR", "PDF", "URL", "SEO", "SaaS", "B2B", "B2C", "KPI", "ИТ", "ПО",
    "НДС", "ФЗ", "ГК", "ИП", "ООО", "АО", "ПАО", "ЗАО", "ОАО", "РФ", "МСК", "QR", "VPN", "USB", "ID", "ОК", "OK",
}
_SENT_END = re.compile(r"[.!?…]\s*$")


@dataclass
class NameHit:
    start: int
    end: int
    type: str
    source: str
    confidence: float
    declinable: bool = True


def _is_cap(word: str) -> bool:
    return word[:1].isupper() and not (len(word) > 1 and word.isupper())


def _name_parses(word: str):
    return [p for p in parse(word.lower()) if any(t in p.tag for t in _NAME_TAGS)]


def _trim(tokens: list[Token], kind: str) -> list[Token]:
    def ok(t: Token) -> bool:
        if not re.search(r"\w", t.text):
            return False
        if t.text.lower() in _ROLE_WORDS:
            return False
        if kind == PiiType.PER:
            return _is_cap(t.text) and (bool(_name_parses(t.text)) or not is_known(t.text))
        return t.pos not in _SKIP_POS_EDGE

    while tokens and not ok(tokens[0]):
        tokens = tokens[1:]
    while tokens and not ok(tokens[-1]):
        tokens = tokens[:-1]
    return tokens


def _sentence_initial(text: str, start: int) -> bool:
    before = text[:start].rstrip(" «\"„“-–—")
    return not before or bool(_SENT_END.search(before)) or before.endswith(":")


def find_names(text: str, tokens: list[Token], *, use_ner: bool = True) -> list[NameHit]:
    """Находки ФИО и организаций в одной реплике. tokens — токены с морфологией этой реплики."""
    hits: list[NameHit] = []
    if use_ner:
        for s, e, typ in ner_spans(text):
            if typ not in ("PER", "ORG"):
                continue
            kind = PiiType.PER if typ == "PER" else PiiType.ORG
            inner = _trim([t for t in tokens if t.start >= s and t.end <= e], kind)
            if not inner:
                continue
            if kind == PiiType.ORG and text[inner[0].start : inner[-1].end] in _ORG_STOP:
                continue
            quoted = kind == PiiType.ORG and bool(re.search(r"[«\"„“]", text[inner[0].start : inner[-1].end]))
            hits.append(NameHit(inner[0].start, inner[-1].end, kind, "ner", 0.9, declinable=not quoted))
        hits = _merge_adjacent(text, hits)

    covered = [(h.start, h.end) for h in hits]
    run: list[Token] = []
    for t in tokens + [Token("", len(text) + 1, len(text) + 1)]:
        if t.text and _dict_name(text, t) and not any(a <= t.start < b for a, b in covered):
            if run and text[run[-1].end : t.start].strip():
                _flush(run, hits)
                run = []
            run.append(t)
        else:
            _flush(run, hits)
            run = []
    return hits


def _dict_name(text: str, t: Token) -> bool:
    if len(t.text) < 2 or not _is_cap(t.text) or not t.text.isalpha() and "-" not in t.text:
        return False
    if t.text.lower() in _ROLE_WORDS:
        return False
    parses = parse(t.text.lower())
    names = [p for p in parses if any(tag in p.tag for tag in _NAME_TAGS)]
    if not names:
        return False
    if _sentence_initial(text, t.start):
        top = parses[0]
        return ("Name" in top.tag or "Patr" in top.tag) and top.score >= 0.5
    return True


def _flush(run: list[Token], hits: list[NameHit]) -> None:
    if run:
        hits.append(NameHit(run[0].start, run[-1].end, PiiType.PER, "dict", 0.6))


def _merge_adjacent(text: str, hits: list[NameHit]) -> list[NameHit]:
    hits = sorted(hits, key=lambda h: h.start)
    out: list[NameHit] = []
    for h in hits:
        if out and out[-1].type == h.type == PiiType.PER and not text[out[-1].end : h.start].strip():
            out[-1].end = h.end
        else:
            out.append(h)
    return out


# ---------------------------------------------------------------- морфология найденного


@dataclass
class MorphInfo:
    gcase: str | None
    gender: str | None
    norm: str


_PM_CASE = {"им": "nomn", "род": "gent", "дат": "datv", "вин": "accs", "тв": "ablt", "пр": "loct"}
_PM_TO_CASE = {v: k for k, v in _PM_CASE.items()} | {"gen2": "род", "loc2": "пр", "voct": "им", "acc2": "вин"}


def analyze(tokens: list[Token], kind: str) -> MorphInfo:
    words = [t for t in tokens if re.search(r"\w", t.text)]
    cases = [UD_TO_CASE[t.feats["Case"]] for t in words if t.feats and t.feats.get("Case") in UD_TO_CASE]
    gcase = Counter(cases).most_common(1)[0][0] if cases else None

    gender = None
    if kind == PiiType.PER:
        for t in words:
            ps = [p for p in parse(t.text.lower()) if "Name" in p.tag]
            if ps and ps[0].tag.gender in ("masc", "femn"):
                gender = ps[0].tag.gender
                break
        if gender is None:
            genders = [t.feats.get("Gender") for t in words if t.feats]
            g = Counter(x for x in genders if x in ("Masc", "Fem")).most_common(1)
            gender = {"Masc": "masc", "Fem": "femn"}[g[0][0]] if g else None
        norm = " ".join(nominative_person(t.text, gcase, gender) for t in words)
    else:
        norm = " ".join(nominative_word(t.text, gcase) for t in words)
    return MorphInfo(gcase, gender, norm.lower())


def nominative_person(word: str, gcase: str | None, gender: str | None) -> str:
    if "-" in word:
        return "-".join(nominative_person(p, gcase, gender) for p in word.split("-"))
    if gcase in (None, "им"):
        return word.lower()
    pm_case = _PM_CASE[gcase]
    ps = [p for p in _name_parses(word) if p.tag.case == pm_case]
    if gender:
        ps = [p for p in ps if (p.tag.gender == gender or "ms-f" in p.tag)] or ps
    for p in ps:
        if "Fixd" in p.tag:
            return word.lower()
        inf = p.inflect({"nomn"})
        if inf:
            return inf.word
    return _deinflect_surname(word.lower(), gcase, gender)


def _deinflect_surname(w: str, gcase: str, gender: str | None) -> str:
    rules_m = {
        "род": [("ова", "ов"), ("ева", "ев"), ("ёва", "ёв"), ("ина", "ин"), ("ына", "ын"), ("ского", "ский"),
                ("цкого", "цкий")],
        "дат": [("ову", "ов"), ("еву", "ев"), ("ёву", "ёв"), ("ину", "ин"), ("ыну", "ын"), ("скому", "ский"),
                ("цкому", "цкий")],
        "вин": [("ова", "ов"), ("ева", "ев"), ("ёва", "ёв"), ("ина", "ин"), ("ына", "ын"), ("ского", "ский"),
                ("цкого", "цкий")],
        "тв": [("овым", "ов"), ("евым", "ев"), ("ёвым", "ёв"), ("иным", "ин"), ("ыным", "ын"), ("ским", "ский"),
               ("цким", "цкий")],
        "пр": [("ове", "ов"), ("еве", "ев"), ("ёве", "ёв"), ("ине", "ин"), ("ыне", "ын"), ("ском", "ский"),
               ("цком", "цкий")],
    }
    rules_f = {
        "род": [("овой", "ова"), ("евой", "ева"), ("иной", "ина"), ("ской", "ская"), ("цкой", "цкая")],
        "дат": [("овой", "ова"), ("евой", "ева"), ("иной", "ина"), ("ской", "ская"), ("цкой", "цкая")],
        "вин": [("ову", "ова"), ("еву", "ева"), ("ину", "ина"), ("скую", "ская"), ("цкую", "цкая")],
        "тв": [("овой", "ова"), ("евой", "ева"), ("иной", "ина"), ("ской", "ская"), ("цкой", "цкая")],
        "пр": [("овой", "ова"), ("евой", "ева"), ("иной", "ина"), ("ской", "ская"), ("цкой", "цкая")],
    }
    table = rules_f if gender == "femn" else rules_m
    for suf, rep in table.get(gcase, []):
        if w.endswith(suf):
            return w[: -len(suf)] + rep
    return w


def nominative_word(word: str, gcase: str | None) -> str:
    if gcase in (None, "им") or not word.isalpha():
        return word.lower()
    pm_case = _PM_CASE[gcase]
    for p in parse(word.lower()):
        if p.tag.case == pm_case and p.tag.POS in ("NOUN", "ADJF", "PRTF"):
            inf = p.inflect({"nomn"})
            if inf:
                return inf.word
    return word.lower()


def tag_utterances(texts: list[str]) -> list[list[Token]]:
    return morph_tag(texts)


def lemmas(text: str) -> list[tuple[Token, str]]:
    return [(t, parse(t.text.lower())[0].normal_form if t.text.isalpha() else t.text.lower()) for t in tokenize(text)]
