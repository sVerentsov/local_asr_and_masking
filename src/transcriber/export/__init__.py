"""Экспорт замаскированного транскрипта и восстановленного ответа в docx и md."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from transcriber.mask import masked_lines
from transcriber.model import Transcript
from transcriber.vault import Vault

INSTRUCTION = (
    "Документ содержит метки вида [ФИО_1:род]. Не раскрывай и не переписывай метки. Если метка в ответе стоит "
    "в другом падеже, меняй только код после двоеточия: им, род, дат, вин, тв, пр"
)
PHONE_EMAIL_NOTE = (
    "Телефоны вида +7 000 000-00-01 и адреса вида user1@example.com — тоже метки, сохраняй их без изменений."
)


def unique_path(directory: Path, name: str) -> Path:
    path = directory / name
    stem, suffix = path.stem, path.suffix
    k = 2
    while path.exists():
        path = directory / f"{stem}_{k}{suffix}"
        k += 1
    return path


def atomic_write(path: Path, write_fn) -> Path:
    """Пишет во временный файл рядом и переименовывает: частично записанный файл не появляется."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=path.suffix, dir=path.parent)
    os.close(fd)
    try:
        write_fn(Path(tmp))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def transcript_markdown(tr: Transcript, vault: Vault) -> str:
    lines = [f"# Транскрипт {vault.code}", "", INSTRUCTION, "", PHONE_EMAIL_NOTE, ""]
    lines += [f"{line}  " for line in masked_lines(tr, vault)]
    return "\n".join(lines).rstrip() + "\n"


def export_transcript(tr: Transcript, vault: Vault, directory: Path, fmt: str = "docx") -> Path:
    if not tr.reviewed:
        raise PermissionError("Экспорт доступен только после отметки «Я проверил»")
    name = f"{vault.code}_транскрипт.{fmt}"
    path = unique_path(directory, name)
    if fmt == "md":
        text = transcript_markdown(tr, vault)
        return atomic_write(path, lambda p: p.write_text(text, encoding="utf-8"))
    if fmt == "docx":
        from transcriber.export.docx import transcript_docx

        lines = masked_lines(tr, vault)
        return atomic_write(path, lambda p: transcript_docx(vault.code, lines, p))
    raise ValueError(f"Неизвестный формат: {fmt}")


def export_answer(text: str, code: str | None, directory: Path, fmt: str = "docx") -> Path:
    name = f"{code or 'ответ'}_ответ.{fmt}"
    path = unique_path(directory, name)
    if fmt == "md":
        return atomic_write(path, lambda p: p.write_text(text, encoding="utf-8"))
    from transcriber.export.docx import markdown_docx

    return atomic_write(path, lambda p: markdown_docx(text, p))
