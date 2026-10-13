"""Замер качества на размеченном наборе (критерии приёмки из ТЗ).

Формат набора — JSONL, одна запись на звонок:
  {"id": "call-01", "utterances": [{"speaker": "Оператор", "text": "..."}],
   "pii": [{"type": "ФИО", "value": "Петрову Ивану", "utt": 3, "case": "дат"}]}
Значения размечаются по тексту реплик. Набор с реальными звонками хранится вне репозитория.

Ответы для проверки обратимости — JSONL: {"id": "call-01", "answer": "...с метками...", "expected": "..."}.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from transcriber.mask import detect_and_assign, masked_lines, normalize_transcript
from transcriber.model import PiiType, Transcript, Utterance
from transcriber.unmask.restore import restore
from transcriber.vault import Vault, digits_of

FORMAT_TYPES = {PiiType.PHONE, PiiType.CARD, PiiType.ACCOUNT, PiiType.PASSPORT, PiiType.INN, PiiType.SNILS}
NAME_TYPES = {PiiType.PER, PiiType.ORG}


def _covered(tr: Transcript, item: dict) -> tuple[bool, str | None]:
    """Покрыто ли размеченное значение находкой; падеж найденной метки."""
    utt = item.get("utt")
    value = item["value"]
    for s in tr.spans:
        for p in s.parts:
            if utt is not None and p.utt != utt or p.field != "text":
                continue
            text = tr.utterances[p.utt].text
            pos = text.lower().find(value.lower())
            if pos >= 0 and p.start <= pos and pos + len(value) <= p.end + 1:
                return True, s.gcase
    d = digits_of(value)
    if len(d) >= 6:
        for s in tr.spans:
            if d in digits_of(s.value) or digits_of(s.value) in d and len(digits_of(s.value)) >= len(d) - 1:
                return True, None
    return False, None


def evaluate_records(records: list[dict]) -> dict:
    stats = {"format": [0, 0], "names": [0, 0], "case": [0, 0], "leaks_before_review": 0, "idempotency_new_spans": 0,
             "spans_total": 0}
    by_type: dict[str, list[int]] = {}
    for rec in records:
        utts = [Utterance(u["text"], u.get("speaker")) for u in rec["utterances"]]
        tr = Transcript(id=rec.get("id", "x"), title="", utterances=utts)
        vault = Vault(code="DEL-EVAL")
        normalize_transcript(tr)
        detect_and_assign(tr, vault)
        stats["spans_total"] += len(tr.spans)
        tr.reviewed = True
        rendered = "\n".join(masked_lines(tr, vault)).lower()
        for item in rec.get("pii", []):
            t = item["type"]
            ok, gcase = _covered(tr, item)
            bucket = "format" if t in FORMAT_TYPES else "names" if t in NAME_TYPES else None
            if bucket:
                stats[bucket][0] += ok
                stats[bucket][1] += 1
            by_type.setdefault(t, [0, 0])
            by_type[t][0] += ok
            by_type[t][1] += 1
            if item.get("case") and ok:
                stats["case"][0] += gcase == item["case"]
                stats["case"][1] += 1
            if item["value"].lower() in rendered:
                stats["leaks_before_review"] += 1
        # Идемпотентность: повторное маскирование замаскированного текста не даёт новых масок.
        again = Transcript(id="again", title="", utterances=[Utterance(re.sub(r"^[^:]*:\s", "", ln))
                                                            for ln in masked_lines(tr, vault)])
        detect_and_assign(again, Vault(code="DEL-EVAL"))
        stats["idempotency_new_spans"] += len(again.spans)

    def rate(x):
        return round(x[0] / x[1], 4) if x[1] else None

    return {
        "records": len(records),
        "recall_format": rate(stats["format"]),
        "recall_names": rate(stats["names"]),
        "case_accuracy_masking": rate(stats["case"]),
        "recall_by_type": {t: rate(v) for t, v in sorted(by_type.items())},
        "leaks_before_review": stats["leaks_before_review"],
        "idempotency_new_spans": stats["idempotency_new_spans"],
        "spans_total": stats["spans_total"],
    }


def evaluate_answers(records: list[dict], answers: list[dict]) -> dict:
    vaults: dict[str, Vault] = {}
    for rec in records:
        utts = [Utterance(u["text"], u.get("speaker")) for u in rec["utterances"]]
        tr = Transcript(id=rec.get("id", "x"), title="", utterances=utts)
        vault = Vault(code="DEL-EVAL")
        normalize_transcript(tr)
        detect_and_assign(tr, vault)
        vaults[rec.get("id", "x")] = vault
    exact = 0
    unknown = 0
    for ans in answers:
        res = restore(ans["answer"], vaults[ans["id"]])
        exact += res.text.strip() == ans["expected"].strip()
        unknown += res.unknown
    return {"answers": len(answers), "exact_restore_rate": round(exact / len(answers), 4) if answers else None,
            "unknown_labels": unknown}


def evaluate_file(dataset: Path, answers: Path | None = None) -> dict:
    records = [json.loads(ln) for ln in dataset.read_text(encoding="utf-8").splitlines() if ln.strip()]
    report = evaluate_records(records)
    if answers:
        ans = [json.loads(ln) for ln in answers.read_text(encoding="utf-8").splitlines() if ln.strip()]
        report["reversibility"] = evaluate_answers(records, ans)
    return report
