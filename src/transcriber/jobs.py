"""Фоновые задачи распознавания: работают в процессе сервера и не зависят от открытой вкладки."""

from __future__ import annotations

import logging
import shutil
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from transcriber.model import new_id

log = logging.getLogger(__name__)


@dataclass
class Job:
    id: str
    case_id: str
    title: str
    status: str = "queued"  # queued | running | done | error
    stage: str = "В очереди"
    progress: float = 0.0
    error: str | None = None
    transcript_id: str | None = None
    created: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


class SafeError(Exception):
    """Ошибка с сообщением для пользователя (без содержимого записи)."""


class JobManager:
    def __init__(self):
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="job")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, case_id: str, title: str, work: Callable[[Job], str], cleanup_dir: Path | None = None) -> Job:
        job = Job(new_id(), case_id, title)
        with self._lock:
            self._jobs[job.id] = job

        def run():
            job.status, job.stage = "running", "Запуск"
            try:
                job.transcript_id = work(job)
                job.status, job.stage, job.progress = "done", "Готово", 1.0
            except SafeError as e:
                job.status, job.error = "error", str(e)
            except Exception as e:  # noqa: BLE001
                # В лог — только тип ошибки: сообщение может содержать фрагменты текста.
                log.error("Задача завершилась с ошибкой: %s", type(e).__name__, extra={"safe": True})
                job.status, job.error = "error", _user_message(e)
            finally:
                if cleanup_dir is not None:
                    shutil.rmtree(cleanup_dir, ignore_errors=True)

        self._pool.submit(run)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self, case_id: str | None = None) -> list[Job]:
        jobs = [j for j in self._jobs.values() if case_id is None or j.case_id == case_id]
        return sorted(jobs, key=lambda j: j.created, reverse=True)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def _user_message(e: Exception) -> str:
    from transcriber.asr import ModelMissing
    from transcriber.audio import AudioError
    from transcriber.importers import ImportError_
    from transcriber.pii.detect import DetectionError

    if isinstance(e, (ModelMissing, AudioError, ImportError_, DetectionError)):
        return str(e)
    if isinstance(e, MemoryError):
        return "Не хватило памяти для обработки записи"
    return "Не удалось обработать файл. Подробности в журнале приложения."
