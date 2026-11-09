from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from shorts.config import Settings
from shorts.database import Database
from shorts.pipeline import run_job


log = logging.getLogger(__name__)


class JobRuntime:
    """Run long video jobs off the request thread and recover due work."""

    def __init__(self, settings: Settings, *, poll_seconds: int = 15):
        self.settings = settings
        self.db = Database(settings.database)
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="shorts-job")
        self.poll_seconds = poll_seconds
        self._active: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None

    def start(self) -> None:
        if self._worker is not None:
            return
        self._worker = threading.Thread(target=self._poll, name="shorts-recovery", daemon=True)
        self._worker.start()

    def close(self) -> None:
        self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout=2)
        self.pool.shutdown(wait=False, cancel_futures=False)

    def enqueue(self, job_id: str, operation: Callable[[], object]) -> bool:
        with self._lock:
            if job_id in self._active:
                return False
            self._active.add(job_id)
        try:
            self.pool.submit(self._run, job_id, operation)
        except RuntimeError:
            with self._lock:
                self._active.discard(job_id)
            raise
        return True

    def _run(self, job_id: str, operation: Callable[[], object]) -> None:
        try:
            operation()
        except Exception:
            # The generation and publishing services persist actionable job errors.
            log.exception("job=%s background operation failed", job_id)
        finally:
            with self._lock:
                self._active.discard(job_id)

    def _poll(self) -> None:
        while not self._stop.is_set():
            try:
                for job_id in self.db.due_jobs():
                    self.enqueue(job_id, lambda job_id=job_id: run_job(job_id, self.db, self.settings))
            except Exception:
                log.exception("background recovery poll failed")
            self._stop.wait(self.poll_seconds)
