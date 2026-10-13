"""Командная строка: каждый шаг конвейера доступен без интерфейса — для автотестов и замеров качества."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from transcriber.config import enforce_offline


def _vault_load(path: Path, code: str | None):
    from transcriber.model import new_case_code
    from transcriber.vault import Vault

    if path.exists():
        return Vault.from_dict(json.loads(path.read_text(encoding="utf-8")))
    return Vault(code=code or new_case_code())


def _vault_save(path: Path, vault) -> None:
    path.write_text(json.dumps(vault.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")


def _load_utterances(path: Path, engine: str, speakers: int | None):
    from transcriber import audio, importers

    ext = path.suffix.lower()
    if ext in importers.SUPPORTED:
        return importers.import_bytes(path.read_bytes(), path.name), None, "text"
    if ext in audio.SUPPORTED:
        from transcriber.pipeline import transcribe_file

        def progress(stage, frac):
            print(f"\r{stage}: {frac:4.0%}   ", end="", file=sys.stderr, flush=True)

        utts, dur = transcribe_file(str(path), engine, speakers, progress)
        print(file=sys.stderr)
        return utts, dur, "audio"
    raise SystemExit(f"Неизвестный формат: {ext}")


def cmd_transcribe(a) -> None:
    from transcriber.mask import fmt_time
    from transcriber.normalize import normalize_numbers

    utts, _, _ = _load_utterances(Path(a.input), a.engine, a.speakers)
    lines = []
    for u in utts:
        head = f"{u.speaker or ''} [{fmt_time(u.start)}]".strip() if u.start is not None else (u.speaker or "")
        text = normalize_numbers(u.text)
        lines.append(f"{head}: {text}" if head else text)
    out = "\n".join(lines) + "\n"
    if a.output:
        Path(a.output).write_text(out, encoding="utf-8")
    else:
        sys.stdout.write(out)


def cmd_mask(a) -> None:
    from transcriber.export import export_transcript, transcript_markdown
    from transcriber.pii.detect import DetectSettings
    from transcriber.pipeline import build_transcript

    vault_path = Path(a.vault)
    vault = _vault_load(vault_path, a.code)
    utts, dur, kind = _load_utterances(Path(a.input), a.engine, a.speakers)
    settings = DetectSettings(org_dictionary=a.org or [])
    tr = build_transcript(utts, vault, title=Path(a.input).stem, source_kind=kind, duration=dur, settings=settings)
    tr.reviewed = True  # в CLI проверки нет: режим для замеров и автотестов
    _vault_save(vault_path, vault)
    if a.spans:
        Path(a.spans).write_text(json.dumps(tr.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    if a.output:
        out = Path(a.output)
        fmt = out.suffix.lstrip(".") or "md"
        path = export_transcript(tr, vault, out.parent, fmt)
        if path.name != out.name:
            path.replace(out)
        print(f"Сохранено: {out}", file=sys.stderr)
    else:
        sys.stdout.write(transcript_markdown(tr, vault))


def cmd_unmask(a) -> None:
    from transcriber.unmask.restore import restore

    vault = _vault_load(Path(a.vault), None)
    from transcriber.importers import decode_text

    data = Path(a.input).read_bytes() if a.input != "-" else sys.stdin.buffer.read()
    text = decode_text(data)
    res = restore(text, vault)
    if a.output:
        Path(a.output).write_text(res.text, encoding="utf-8")
    else:
        sys.stdout.write(res.text)
    print(f"Восстановлено: {res.restored}, неизвестных меток: {res.unknown}, падеж не определён: {res.uncertain}",
          file=sys.stderr)


def cmd_models(a) -> None:
    from transcriber import models

    if a.action == "status":
        for name, ok in models.status().items():
            print(f"{'✓' if ok else '✗'} {name}: {models.MODELS[name].title}")
        return
    if a.action == "lock":
        models.write_lock(Path(a.lock_file))
        return
    names = a.only or [n for n, m in models.MODELS.items() if m.required or a.all]

    def progress(fname, done, total):
        pct = f"{done / total:4.0%}" if total else f"{done >> 20} МБ"
        print(f"\r  {fname}: {pct}   ", end="", file=sys.stderr, flush=True)

    for name in names:
        print(f"{models.MODELS[name].title}", file=sys.stderr)
        models.download(name, force=a.force, progress=progress)
        print(file=sys.stderr)


def cmd_serve(a) -> None:
    from transcriber.web.app import serve

    serve(port=a.port, open_browser=not a.no_browser)


def cmd_eval(a) -> None:
    from transcriber.evaluate import evaluate_file

    report = evaluate_file(Path(a.dataset), Path(a.answers) if a.answers else None)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="transcriber", description="Локальный транскрибатор с маскированием ПД")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("serve", help="запустить интерфейс на 127.0.0.1")
    s.add_argument("--port", type=int, default=None)
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(func=cmd_serve)

    def common(sp):
        sp.add_argument("input", help="аудио (mp3, m4a, wav…) или транскрипт (txt, md, docx, srt, vtt)")
        sp.add_argument("--engine", choices=["gigaam", "tone"], default="gigaam")
        sp.add_argument("--speakers", type=int, default=None, help="число собеседников (по умолчанию авто)")

    s = sub.add_parser("transcribe", help="распознать запись без маскирования")
    common(s)
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_transcribe)

    s = sub.add_parser("mask", help="замаскировать запись или транскрипт")
    common(s)
    s.add_argument("--vault", required=True, help="JSON-словарь соответствий (содержит ПД!)")
    s.add_argument("--code", help="код дела для нового словаря")
    s.add_argument("--org", action="append", help="название организации из словаря пользователя")
    s.add_argument("-o", "--output", help="файл .docx или .md; без него — markdown в stdout")
    s.add_argument("--spans", help="сохранить транскрипт с находками в JSON")
    s.set_defaults(func=cmd_mask)

    s = sub.add_parser("unmask", help="вернуть данные в ответ Claude")
    s.add_argument("input", help="файл с ответом или - для stdin")
    s.add_argument("--vault", required=True)
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_unmask)

    s = sub.add_parser("models", help="модели распознавания и диаризации")
    s.add_argument("action", choices=["download", "status", "lock"])
    s.add_argument("--only", action="append", help="только указанная модель")
    s.add_argument("--all", action="store_true", help="включая необязательные (T-one)")
    s.add_argument("--force", action="store_true")
    s.add_argument("--lock-file", default="models.lock.json")
    s.set_defaults(func=cmd_models)

    s = sub.add_parser("eval", help="метрики качества на размеченном наборе")
    s.add_argument("dataset", help="JSONL с репликами и разметкой ПД")
    s.add_argument("--answers", help="JSONL с ответами Claude для проверки обратимости")
    s.set_defaults(func=cmd_eval)

    a = p.parse_args(argv)
    if a.cmd != "models" or a.action != "download":
        enforce_offline()
    if a.cmd is None:
        enforce_offline()
        cmd_serve(argparse.Namespace(port=None, no_browser=False))
        return
    a.func(a)


def app_main() -> None:
    """Точка входа запускалки: поднимает сервер (или открывает вкладку, если он уже работает)."""
    enforce_offline()
    from transcriber.web.app import serve

    serve(open_browser=True)


if __name__ == "__main__":
    main()
