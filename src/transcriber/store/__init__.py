"""Шифрованное хранилище дел.

SQLite + шифрование содержимого каждой записи AES-256-GCM, ключ из пароля приложения через Argon2id.
Открытым текстом лежат только идентификаторы, коды дел и время: названия дел, словари и транскрипты
зашифрованы. Вместо SQLCipher, потому что у него нет надёжных колёс под все целевые платформы.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from transcriber.model import Transcript, new_case_code, new_id
from transcriber.vault import Vault

_KDF = {"time_cost": 3, "memory_cost": 64 * 1024, "parallelism": 4}
_VERIFIER = b"transcriber-key-ok"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v BLOB);
CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, code TEXT UNIQUE, created REAL, updated REAL, blob BLOB);
CREATE TABLE IF NOT EXISTS transcripts (
    id TEXT PRIMARY KEY, case_id TEXT REFERENCES cases(id), created REAL, blob BLOB
);
"""


class LockedError(RuntimeError):
    pass


class WrongPassword(ValueError):
    pass


@dataclass
class Case:
    id: str
    code: str
    name: str
    created: float
    updated: float
    vault: Vault
    org_dictionary: list[str] = field(default_factory=list)

    def payload(self) -> dict:
        return {"name": self.name, "vault": self.vault.to_dict(), "org_dictionary": self.org_dictionary}


DEFAULT_SETTINGS = {
    "retention_days": 30,
    "asr_engine": "gigaam",
    "types": None,  # None — все типы
    "default_speakers": "auto",
}


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(path.parent, 0o700)
        self.path = path
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA secure_delete=ON")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(SCHEMA)
        if os.name == "posix":
            os.chmod(path, 0o600)
        self._lock = threading.RLock()
        self._aead: AESGCM | None = None

    # ------------------------------------------------------------ ключ
    def _meta(self, k: str) -> bytes | None:
        row = self._db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return row[0] if row else None

    def _set_meta(self, k: str, v: bytes) -> None:
        self._db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (k, v))

    @property
    def initialized(self) -> bool:
        return self._meta("salt") is not None

    @property
    def unlocked(self) -> bool:
        return self._aead is not None

    @staticmethod
    def _derive(password: str, salt: bytes, params: dict) -> AESGCM:
        key = hash_secret_raw(password.encode("utf-8"), salt, hash_len=32, type=Type.ID, **params)
        return AESGCM(key)

    def init(self, password: str) -> None:
        if self.initialized:
            raise RuntimeError("Пароль уже задан")
        if len(password) < 8:
            raise ValueError("Пароль должен быть не короче 8 символов")
        salt = secrets.token_bytes(16)
        with self._lock:
            self._set_meta("salt", salt)
            self._set_meta("kdf", json.dumps(_KDF).encode())
            self._aead = self._derive(password, salt, _KDF)
            self._set_meta("verifier", self._enc(_VERIFIER, b"verifier"))

    def unlock(self, password: str) -> None:
        salt = self._meta("salt")
        if salt is None:
            raise RuntimeError("Пароль ещё не задан")
        params = json.loads(self._meta("kdf") or b"{}") or _KDF
        aead = self._derive(password, salt, params)
        try:
            nonce_ct = self._meta("verifier")
            ok = aead.decrypt(nonce_ct[:12], nonce_ct[12:], b"verifier") == _VERIFIER
        except InvalidTag:
            ok = False
        if not ok:
            raise WrongPassword("Неверный пароль")
        self._aead = aead

    def lock(self) -> None:
        self._aead = None

    def _enc(self, data: bytes, aad: bytes) -> bytes:
        if self._aead is None:
            raise LockedError("Хранилище заблокировано")
        nonce = secrets.token_bytes(12)
        return nonce + self._aead.encrypt(nonce, data, aad)

    def _dec(self, blob: bytes, aad: bytes) -> bytes:
        if self._aead is None:
            raise LockedError("Хранилище заблокировано")
        return self._aead.decrypt(blob[:12], blob[12:], aad)

    def _enc_json(self, obj, aad: str) -> bytes:
        return self._enc(json.dumps(obj, ensure_ascii=False).encode("utf-8"), aad.encode())

    def _dec_json(self, blob: bytes, aad: str):
        return json.loads(self._dec(blob, aad.encode()).decode("utf-8"))

    # ------------------------------------------------------------ настройки
    def get_settings(self) -> dict:
        blob = self._meta("settings")
        data = self._dec_json(blob, "settings") if blob else {}
        return {**DEFAULT_SETTINGS, **data}

    def save_settings(self, settings: dict) -> None:
        with self._lock:
            self._set_meta("settings", self._enc_json(settings, "settings"))

    # ------------------------------------------------------------ дела
    def create_case(self, name: str) -> Case:
        with self._lock:
            for _ in range(20):
                code = new_case_code()
                if not self._db.execute("SELECT 1 FROM cases WHERE code=?", (code,)).fetchone():
                    break
            now = time.time()
            case = Case(new_id(), code, name.strip() or code, now, now, Vault(code=code))
            self._db.execute("INSERT INTO cases (id, code, created, updated, blob) VALUES (?,?,?,?,?)",
                             (case.id, code, now, now, self._enc_json(case.payload(), "case:" + case.id)))
            return case

    def _case_from_row(self, row) -> Case:
        cid, code, created, updated, blob = row
        d = self._dec_json(blob, "case:" + cid)
        return Case(cid, code, d["name"], created, updated, Vault.from_dict(d["vault"]), d.get("org_dictionary", []))

    def list_cases(self) -> list[Case]:
        rows = self._db.execute("SELECT id, code, created, updated, blob FROM cases ORDER BY updated DESC").fetchall()
        return [self._case_from_row(r) for r in rows]

    def get_case(self, case_id: str) -> Case:
        row = self._db.execute("SELECT id, code, created, updated, blob FROM cases WHERE id=?", (case_id,)).fetchone()
        if row is None:
            raise KeyError("Дело не найдено")
        return self._case_from_row(row)

    def find_case_by_code(self, code: str) -> Case | None:
        row = self._db.execute("SELECT id FROM cases WHERE code=?", (code.upper(),)).fetchone()
        return self.get_case(row[0]) if row else None

    def save_case(self, case: Case) -> None:
        with self._lock:
            case.updated = time.time()
            self._db.execute("UPDATE cases SET updated=?, blob=? WHERE id=?",
                             (case.updated, self._enc_json(case.payload(), "case:" + case.id), case.id))

    def delete_case(self, case_id: str) -> None:
        with self._lock:
            # Перезапись содержимого перед удалением, затем VACUUM: оригиналы не остаются в свободных страницах.
            for (tid,) in self._db.execute("SELECT id FROM transcripts WHERE case_id=?", (case_id,)).fetchall():
                self._wipe("transcripts", tid)
            self._wipe("cases", case_id)
            self._db.execute("DELETE FROM transcripts WHERE case_id=?", (case_id,))
            self._db.execute("DELETE FROM cases WHERE id=?", (case_id,))
            self._db.execute("VACUUM")

    def _wipe(self, table: str, row_id: str) -> None:
        row = self._db.execute(f"SELECT length(blob) FROM {table} WHERE id=?", (row_id,)).fetchone()
        if row and row[0]:
            self._db.execute(f"UPDATE {table} SET blob=? WHERE id=?", (secrets.token_bytes(row[0]), row_id))

    def purge_expired(self, days: int) -> int:
        if not days or days <= 0:
            return 0
        cutoff = time.time() - days * 86400
        ids = [r[0] for r in self._db.execute("SELECT id FROM cases WHERE updated < ?", (cutoff,)).fetchall()]
        for cid in ids:
            self.delete_case(cid)
        return len(ids)

    # ------------------------------------------------------------ транскрипты
    def save_transcript(self, case_id: str, tr: Transcript) -> None:
        with self._lock:
            blob = self._enc_json(tr.to_dict(), "tr:" + tr.id)
            self._db.execute(
                "INSERT INTO transcripts (id, case_id, created, blob) VALUES (?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET blob=excluded.blob",
                (tr.id, case_id, tr.created, blob),
            )
            self._db.execute("UPDATE cases SET updated=? WHERE id=?", (time.time(), case_id))

    def get_transcript(self, tr_id: str) -> tuple[str, Transcript]:
        row = self._db.execute("SELECT case_id, blob FROM transcripts WHERE id=?", (tr_id,)).fetchone()
        if row is None:
            raise KeyError("Транскрипт не найден")
        return row[0], Transcript.from_dict(self._dec_json(row[1], "tr:" + tr_id))

    def list_transcripts(self, case_id: str) -> list[Transcript]:
        rows = self._db.execute("SELECT id, blob FROM transcripts WHERE case_id=? ORDER BY created",
                                (case_id,)).fetchall()
        return [Transcript.from_dict(self._dec_json(b, "tr:" + i)) for i, b in rows]

    def delete_transcript(self, tr_id: str) -> None:
        with self._lock:
            self._wipe("transcripts", tr_id)
            self._db.execute("DELETE FROM transcripts WHERE id=?", (tr_id,))
            self._db.execute("VACUUM")

    def close(self) -> None:
        self._db.close()
