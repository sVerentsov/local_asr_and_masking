"""Локальный сервер интерфейса: только 127.0.0.1, сессия по cookie, проверка Host от DNS-rebinding."""

from __future__ import annotations

import logging
import mimetypes
import os
import secrets
import tempfile
import threading
import time
import webbrowser
from contextlib import contextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from transcriber import __version__
from transcriber.config import HOST, PORT, db_path, downloads_dir, enforce_offline, setup_logging
from transcriber.jobs import JobManager, SafeError
from transcriber.model import ALL_TYPES, CASES, TYPE_TITLES, PiiType
from transcriber.store import Case, LockedError, Store, WrongPassword

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
# На Windows реестр иногда отдаёт .js как text/plain, и браузер отказывается выполнять скрипт.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
MAX_UPLOAD = 4 * 1024**3
COOKIE = "transcriber_session"


class State:
    def __init__(self):
        self.store: Store | None = None
        self.jobs = JobManager()
        self.session: str | None = None
        self.case_locks: dict[str, threading.RLock] = {}
        self.port = PORT
        self.server = None

    @contextmanager
    def case_lock(self, case_id: str):
        lock = self.case_locks.setdefault(case_id, threading.RLock())
        with lock:
            yield


S = State()
app = FastAPI(title="Транскрибатор", docs_url=None, redoc_url=None, openapi_url=None)


# ---------------------------------------------------------------- безопасность


@app.middleware("http")
async def guard(request: Request, call_next):
    host = (request.headers.get("host") or "").split(":")[0]
    if host not in ("127.0.0.1", "localhost"):
        return JSONResponse({"detail": "Доступ только с этого компьютера"}, status_code=403)
    path = request.url.path
    open_paths = ("/api/state", "/api/setup", "/api/unlock", "/api/health")
    if path.startswith("/api/"):
        # Нестандартный заголовок требует CORS-preflight, который сервер не разрешает: чужие страницы
        # в браузере не смогут отправить команды локальному серверу.
        if request.method not in ("GET", "HEAD") and request.headers.get("x-requested-with") != "transcriber":
            return JSONResponse({"detail": "Запрос отклонён"}, status_code=403)
        if path not in open_paths and (
            not S.session or not secrets.compare_digest(request.cookies.get(COOKIE, ""), S.session)
        ):
            return JSONResponse({"detail": "Требуется пароль"}, status_code=401)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'"
    )
    return response


@app.exception_handler(LockedError)
async def _locked(_r, _e):
    return JSONResponse({"detail": "Хранилище заблокировано"}, status_code=401)


@app.exception_handler(KeyError)
async def _missing(_r, e):
    return JSONResponse({"detail": str(e.args[0]) if e.args else "Не найдено"}, status_code=404)


@app.exception_handler(ValueError)
async def _bad(_r, e):
    return JSONResponse({"detail": str(e)}, status_code=400)


@app.exception_handler(PermissionError)
async def _forbidden(_r, e):
    return JSONResponse({"detail": str(e)}, status_code=409)


def _store() -> Store:
    if S.store is None or not S.store.unlocked:
        raise LockedError()
    return S.store


def _start_session(response: Response) -> None:
    S.session = secrets.token_urlsafe(32)
    response.set_cookie(COOKIE, S.session, httponly=True, samesite="strict", secure=False)


# ---------------------------------------------------------------- состояние и пароль


@app.get("/api/health")
def health():
    return {"ok": True, "app": "transcriber", "version": __version__}


@app.get("/api/state")
def state(request: Request):
    from transcriber import models

    st = S.store
    authed = bool(S.session) and secrets.compare_digest(request.cookies.get(COOKIE, ""), S.session or "")
    return {
        "version": __version__,
        "initialized": bool(st and st.initialized),
        "unlocked": bool(st and st.unlocked and authed),
        "models": models.status(),
        "types": [{"code": t.value, "title": TYPE_TITLES[t]} for t in PiiType],
        "cases": CASES,
    }


@app.post("/api/setup")
def setup(response: Response, password: str = Body(..., embed=True)):
    st = S.store
    if st.initialized:
        raise HTTPException(409, "Пароль уже задан")
    st.init(password)
    _start_session(response)
    return {"ok": True}


@app.post("/api/unlock")
def unlock(response: Response, password: str = Body(..., embed=True)):
    st = S.store
    try:
        st.unlock(password)
    except WrongPassword:
        time.sleep(1.0)
        raise HTTPException(403, "Неверный пароль") from None
    _start_session(response)
    days = st.get_settings().get("retention_days") or 0
    st.purge_expired(int(days))
    return {"ok": True}


@app.post("/api/lock")
def lock():
    S.store.lock()
    S.session = None
    return {"ok": True}


# ---------------------------------------------------------------- дела


def _case_summary(c: Case, n_tr: int | None = None) -> dict:
    return {"id": c.id, "code": c.code, "name": c.name, "created": c.created, "updated": c.updated,
            "org_dictionary": c.org_dictionary, "transcripts": n_tr}


@app.get("/api/cases")
def list_cases():
    st = _store()
    return [_case_summary(c, len(st.list_transcripts(c.id))) for c in st.list_cases()]


@app.post("/api/cases")
def create_case(name: str = Body("", embed=True)):
    return _case_summary(_store().create_case(name), 0)


@app.get("/api/cases/{case_id}")
def get_case(case_id: str):
    st = _store()
    c = st.get_case(case_id)
    trs = st.list_transcripts(case_id)
    return {
        **_case_summary(c, len(trs)),
        "transcripts_list": [
            {"id": t.id, "title": t.title, "created": t.created, "reviewed": t.reviewed, "duration": t.duration,
             "source_kind": t.source_kind, "utterances": len(t.utterances), "spans": len(t.spans)}
            for t in trs
        ],
        "jobs": [j.to_dict() for j in S.jobs.list(case_id)],
    }


@app.patch("/api/cases/{case_id}")
def update_case(case_id: str, payload: dict = Body(...)):
    st = _store()
    with S.case_lock(case_id):
        c = st.get_case(case_id)
        if "name" in payload:
            c.name = str(payload["name"]).strip() or c.code
        if "org_dictionary" in payload:
            c.org_dictionary = [str(x).strip() for x in payload["org_dictionary"] if str(x).strip()]
        st.save_case(c)
    return _case_summary(c)


@app.delete("/api/cases/{case_id}")
def delete_case(case_id: str):
    with S.case_lock(case_id):
        _store().delete_case(case_id)
    return {"ok": True}


# ---------------------------------------------------------------- загрузка и задачи


def _detect_settings(st: Store, case: Case):
    from transcriber.pii.detect import DetectSettings

    types = st.get_settings().get("types") or list(ALL_TYPES)
    return DetectSettings(types=types, org_dictionary=case.org_dictionary)


@app.post("/api/cases/{case_id}/upload")
async def upload(case_id: str, request: Request, filename: str, speakers: str = "auto"):
    from transcriber import audio, importers

    st = _store()
    st.get_case(case_id)
    ext = Path(filename).suffix.lower()
    is_text = ext in importers.SUPPORTED
    if not is_text and ext not in audio.SUPPORTED:
        raise HTTPException(400, f"Формат {ext or 'без расширения'} не поддерживается")
    n_speakers = None if speakers in ("", "auto") else max(1, min(10, int(speakers)))

    # Исходный файл не копируется в хранилище: он живёт во временном каталоге с правами 700 до конца обработки.
    tmp_dir = Path(tempfile.mkdtemp(prefix="transcriber-"))
    path = tmp_dir / f"input{ext}"
    size = 0
    try:
        with path.open("wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, "Файл слишком большой")
                f.write(chunk)
    except BaseException:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    title = Path(filename).stem[:80]
    engine = st.get_settings().get("asr_engine", "gigaam")

    def work(job) -> str:
        from transcriber.pipeline import build_transcript, transcribe_file

        def progress(stage: str, frac: float) -> None:
            weights = {"Декодирование": (0.0, 0.05), "Распознавание": (0.05, 0.75),
                       "Разделение по спикерам": (0.75, 0.95), "Поиск персональных данных": (0.95, 1.0)}
            a, b = weights.get(stage, (0.0, 1.0))
            job.stage, job.progress = stage, a + (b - a) * max(0.0, min(1.0, frac))

        if is_text:
            job.stage = "Разбор транскрипта"
            utts, duration, kind = importers.import_bytes(path.read_bytes(), filename), None, "text"
        else:
            utts, duration = transcribe_file(str(path), engine, n_speakers, progress)
            kind = "audio"
        path.unlink(missing_ok=True)
        if not utts:
            raise SafeError("В записи не найдена речь")
        with S.case_lock(case_id):
            case = st.get_case(case_id)
            tr = build_transcript(utts, case.vault, title=title, source_kind=kind, duration=duration,
                                  settings=_detect_settings(st, case), progress=progress)
            st.save_case(case)
            st.save_transcript(case_id, tr)
        return tr.id

    job = S.jobs.submit(case_id, title, work, cleanup_dir=tmp_dir)
    return job.to_dict()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = S.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    return job.to_dict()


# ---------------------------------------------------------------- проверка


def _load_tr(tr_id: str):
    st = _store()
    case_id, tr = st.get_transcript(tr_id)
    return st, st.get_case(case_id), tr


def _review_payload(case: Case, tr) -> dict:
    from transcriber.mask import label_for
    from transcriber.review import suggest_merges, unmasked_occurrences

    vault = case.vault
    spans = []
    mentions: dict[str, int] = {}
    for s in tr.spans:
        ent = vault.get(s.entity_id)
        if ent is None:
            continue
        mentions[ent.id] = mentions.get(ent.id, 0) + 1
        spans.append({
            "id": s.id, "type": s.type, "parts": [p.__dict__ for p in s.parts], "value": s.value,
            "label": label_for(s, vault), "entity_id": ent.id, "source": s.source, "confidence": s.confidence,
            "gcase": s.gcase, "case_flag": s.case_flag, "declinable": ent.declinable,
        })
    entities = []
    for eid, n in mentions.items():
        e = vault.get(eid)
        entities.append({"id": e.id, "type": e.type, "n": e.n, "value": e.value, "variants": e.variants,
                         "mentions": n, "label": vault.label(e, "им" if e.declinable else None),
                         "declinable": e.declinable})
    entities.sort(key=lambda e: (ALL_TYPES.index(e["type"]), e["n"]))
    speakers = []
    for u in tr.utterances:
        if u.speaker is not None and u.speaker not in [s["raw"] for s in speakers]:
            speakers.append({"raw": u.speaker, "display": tr.speaker_name(u.speaker)})
    leaks = [{"entity_id": lk.entity_id, "parts": [p.__dict__ for p in lk.parts],
              "value": vault.get(lk.entity_id).value} for lk in unmasked_occurrences(tr, vault)]
    return {
        "id": tr.id, "title": tr.title, "case_id": case.id, "case_code": case.code, "reviewed": tr.reviewed,
        "utterances": [{"text": u.text, "speaker": u.speaker, "display": tr.speaker_name(u.speaker),
                        "start": u.start} for u in tr.utterances],
        "spans": spans, "entities": entities, "speakers": speakers, "leaks": leaks,
        "suggestions": [s.__dict__ for s in suggest_merges(vault, set(mentions))],
    }


def _save(st: Store, case: Case, tr) -> dict:
    st.save_case(case)
    st.save_transcript(case.id, tr)
    return _review_payload(case, tr)


@app.get("/api/transcripts/{tr_id}")
def get_transcript(tr_id: str):
    _st, case, tr = _load_tr(tr_id)
    return _review_payload(case, tr)


@app.delete("/api/transcripts/{tr_id}")
def delete_transcript(tr_id: str):
    st, case, _tr = _load_tr(tr_id)
    with S.case_lock(case.id):
        st.delete_transcript(tr_id)
    return {"ok": True}


def _mutate(tr_id: str, fn):
    st = _store()
    case_id, _ = st.get_transcript(tr_id)
    with S.case_lock(case_id):
        _st, case, tr = _load_tr(tr_id)
        fn(st, case, tr)
        return _save(st, case, tr)


@app.post("/api/transcripts/{tr_id}/spans")
def add_span(tr_id: str, payload: dict = Body(...)):
    from transcriber.review import add_span as _add

    t = payload.get("type")
    if t not in ALL_TYPES:
        raise HTTPException(400, "Неизвестный тип")
    return _mutate(tr_id, lambda st, case, tr: _add(tr, case.vault, int(payload["utt"]), int(payload["start"]),
                                                   int(payload["end"]), t,
                                                   all_occurrences=bool(payload.get("all", True))))


@app.delete("/api/transcripts/{tr_id}/spans/{span_id}")
def remove_span(tr_id: str, span_id: str, all: int = 0):
    from transcriber.review import remove_span as _rm

    return _mutate(tr_id, lambda st, case, tr: _rm(tr, span_id, all_of_entity=bool(all)))


@app.post("/api/transcripts/{tr_id}/spans/{span_id}/case")
def set_span_case(tr_id: str, span_id: str, gcase: str = Body(..., embed=True)):
    from transcriber.review import set_case

    if gcase not in CASES:
        raise HTTPException(400, "Неизвестный падеж")
    return _mutate(tr_id, lambda st, case, tr: set_case(tr, span_id, gcase))


@app.post("/api/transcripts/{tr_id}/merge")
def merge(tr_id: str, keep: str = Body(...), drop: str = Body(...)):
    from transcriber.review import merge_entities

    def fn(st, case, tr):
        others = [t for t in st.list_transcripts(case.id) if t.id != tr.id]
        merge_entities([tr, *others], case.vault, keep, drop)
        for t in others:
            st.save_transcript(case.id, t)

    return _mutate(tr_id, fn)


@app.post("/api/transcripts/{tr_id}/speakers")
def rename_speaker(tr_id: str, raw: str = Body(...), display: str = Body(...)):
    from transcriber.review import rename_speaker as _rename

    return _mutate(tr_id, lambda st, case, tr: _rename(tr, case.vault, raw, display))


@app.post("/api/transcripts/{tr_id}/leaks/{entity_id}")
def mask_leak(tr_id: str, entity_id: str):
    from transcriber.review import mask_leak as _mask
    from transcriber.review import unmasked_occurrences

    def fn(st, case, tr):
        for lk in unmasked_occurrences(tr, case.vault):
            if lk.entity_id == entity_id:
                _mask(tr, case.vault, entity_id, lk.parts)

    return _mutate(tr_id, fn)


@app.post("/api/transcripts/{tr_id}/reviewed")
def set_reviewed(tr_id: str, reviewed: bool = Body(..., embed=True)):
    def fn(st, case, tr):
        tr.reviewed = reviewed

    return _mutate(tr_id, fn)


@app.get("/api/transcripts/{tr_id}/preview")
def preview(tr_id: str):
    from transcriber.mask import masked_lines

    _st, case, tr = _load_tr(tr_id)
    return {"code": case.code, "lines": masked_lines(tr, case.vault)}


@app.post("/api/transcripts/{tr_id}/export")
def export(tr_id: str, fmt: str = Body("docx", embed=True)):
    from transcriber.export import export_transcript

    if fmt not in ("docx", "md"):
        raise HTTPException(400, "Неизвестный формат")
    _st, case, tr = _load_tr(tr_id)
    path = export_transcript(tr, case.vault, downloads_dir(), fmt)
    return {"filename": path.name, "directory": str(path.parent)}


# ---------------------------------------------------------------- демаскирование


@app.post("/api/unmask")
def unmask(text: str = Body(...), case_id: str | None = Body(None)):
    from transcriber.unmask.parser import find_case_code
    from transcriber.unmask.restore import restore

    st = _store()
    case = None
    if case_id:
        case = st.get_case(case_id)
    else:
        code = find_case_code(text)
        case = st.find_case_by_code(code) if code else None
        if case is None:
            raise HTTPException(422, "Код дела в тексте не найден — выберите дело из списка")
    res = restore(text, case.vault)
    return {"case_id": case.id, "code": case.code, "name": case.name, "text": res.text,
            "highlights": [h.__dict__ for h in res.highlights], "restored": res.restored, "unknown": res.unknown,
            "uncertain": res.uncertain}


@app.post("/api/unmask/export")
def unmask_export(text: str = Body(...), code: str | None = Body(None), fmt: str = Body("docx")):
    from transcriber.export import export_answer

    if fmt not in ("docx", "md"):
        raise HTTPException(400, "Неизвестный формат")
    path = export_answer(text, code, downloads_dir(), fmt)
    return {"filename": path.name, "directory": str(path.parent)}


# ---------------------------------------------------------------- настройки и сервис


@app.get("/api/settings")
def get_settings():
    return _store().get_settings()


@app.put("/api/settings")
def put_settings(payload: dict = Body(...)):
    st = _store()
    cur = st.get_settings()
    if "retention_days" in payload:
        cur["retention_days"] = max(0, min(3650, int(payload["retention_days"])))
    if payload.get("asr_engine") in ("gigaam", "tone"):
        cur["asr_engine"] = payload["asr_engine"]
    if "types" in payload:
        cur["types"] = [t for t in payload["types"] if t in ALL_TYPES] or None
    st.save_settings(cur)
    return cur


@app.post("/api/update/check")
def check_update():
    from transcriber.updates import check

    return check()


@app.post("/api/update/apply")
def apply_update():
    from transcriber.updates import apply

    return apply()


@app.post("/api/shutdown")
def shutdown():
    def stop():
        time.sleep(0.5)
        if S.store:
            S.store.lock()
        if S.server is not None:
            S.server.should_exit = True
        else:
            os._exit(0)

    threading.Thread(target=stop, daemon=True).start()
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ---------------------------------------------------------------- запуск


def _already_running(port: int) -> bool:
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/health", timeout=1) as r:  # noqa: S310
            return json.load(r).get("app") == "transcriber"
    except Exception:  # noqa: BLE001
        return False


def serve(port: int | None = None, open_browser: bool = True, store_path: Path | None = None) -> None:
    import uvicorn

    enforce_offline()
    setup_logging()
    port = port or int(os.environ.get("TRANSCRIBER_PORT", PORT))
    url = f"http://{HOST}:{port}/"
    if _already_running(port):
        if open_browser:
            webbrowser.open(url)
        return
    S.port = port
    S.store = Store(store_path or db_path())
    config = uvicorn.Config(app, host=HOST, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    S.server = server
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    # Модели NLP грузятся в фоне, чтобы интерфейс открылся сразу.
    threading.Thread(target=_warmup, daemon=True).start()
    server.run()
    S.jobs.shutdown()


def _warmup() -> None:
    try:
        from transcriber.nlp import warmup

        warmup()
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось прогреть NLP: %s", type(e).__name__, extra={"safe": True})
