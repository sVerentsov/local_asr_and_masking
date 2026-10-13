"""Ленивая загрузка Natasha (slovnet NER и морфология) и pymorphy3.

Пакет natasha используется только как источник файлов моделей: его __init__ тянет pymorphy2,
который не работает на Python 3.11+, поэтому модули natasha не импортируются.
"""

from __future__ import annotations

import importlib.util
import os
import threading
from dataclasses import dataclass
from functools import lru_cache

from razdel import tokenize as _razdel_tokenize

_lock = threading.Lock()
_models: dict = {}


def _natasha_data() -> str:
    spec = importlib.util.find_spec("natasha")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError("Не найдены модели Natasha (пакет natasha)")
    return os.path.join(list(spec.submodule_search_locations)[0], "data")


def _load() -> dict:
    with _lock:
        if not _models:
            from navec import Navec
            from slovnet import NER, Morph

            data = _natasha_data()
            navec = Navec.load(os.path.join(data, "emb", "navec_news_v1_1B_250K_300d_100q.tar"))
            _models["ner"] = NER.load(os.path.join(data, "model", "slovnet_ner_news_v1.tar")).navec(navec)
            _models["morph"] = Morph.load(
                os.path.join(data, "model", "slovnet_morph_news_v1.tar"), batch_size=16
            ).navec(navec)
    return _models


@lru_cache(maxsize=1)
def morph_analyzer():
    import pymorphy3

    return pymorphy3.MorphAnalyzer()


@lru_cache(maxsize=200_000)
def parse(word: str):
    return morph_analyzer().parse(word)


@lru_cache(maxsize=200_000)
def is_known(word: str) -> bool:
    return morph_analyzer().word_is_known(word.lower())


@dataclass
class Token:
    text: str
    start: int
    end: int
    pos: str = ""
    feats: dict | None = None


def tokenize(text: str) -> list[Token]:
    return [Token(t.text, t.start, t.stop) for t in _razdel_tokenize(text)]


def ner_spans(text: str) -> list[tuple[int, int, str]]:
    if not text.strip():
        return []
    markup = _load()["ner"](text)
    return [(s.start, s.stop, s.type) for s in markup.spans]


def morph_tag(texts: list[str]) -> list[list[Token]]:
    """Морфологический разбор по контексту предложения для каждого текста."""
    toks = [tokenize(t) for t in texts]
    idx = [i for i, ts in enumerate(toks) if ts]
    if idx:
        morph = _load()["morph"]
        for i, markup in zip(idx, morph.map([[t.text for t in toks[i]] for i in idx])):
            for tok, mt in zip(toks[i], markup.tokens):
                tok.pos = mt.pos
                tok.feats = dict(mt.feats)
    return toks


def warmup() -> None:
    _load()
    morph_analyzer()
