"""Background OMR jobs, held in memory.

OMR takes minutes per page, so a whole sheet can run for an hour. Doing that
inside one HTTP request didn't work: the reverse proxy times the request out
long before it finishes, and any backend restart (every deploy) kills it
with nothing to show for it. Instead the upload starts a job here and
returns at once; the client polls for progress and fetches the result when
it's ready.

Zero-server-storage still holds: nothing here touches disk. A job's PDF
bytes are dropped as soon as OMR has run, and its result lives only in this
process's memory until the client collects it (DELETE) or it expires. A
restart loses every job, which the client handles by resubmitting the PDF
it keeps in IndexedDB.
"""

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Literal

from app.models import ProcessScoreResponse

JobState = Literal["queued", "running", "done", "failed"]

# How long a finished job's result is kept for a client that hasn't come
# back for it -- long enough to cover a phone that was locked for a while,
# short enough that abandoned results don't pile up in memory.
RESULT_TTL_SECONDS = 6 * 60 * 60


@dataclass
class Job:
    id: str
    status: JobState = "queued"
    pages_done: int = 0
    pages_total: int | None = None
    error: str | None = None
    result: ProcessScoreResponse | None = None
    created_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None


Runner = Callable[[bytes, Callable[[int, int], None]], ProcessScoreResponse]


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        # One job at a time: OMR already spreads a single sheet's pages over
        # as many processes as free memory allows (see omr._worker_count),
        # so running two sheets side by side would only contend for that
        # memory and risk getting both OOM-killed.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="omr-job")

    def submit(self, job_id: str, pdf_bytes: bytes, run: Runner) -> Job:
        """Start a job, or return the existing one with this id.

        Idempotent so a client that lost track of whether its upload landed
        (refresh, flaky network) can simply submit again. A failed job is
        replaced, which is how a retry happens.
        """
        with self._lock:
            self._prune()
            existing = self._jobs.get(job_id)
            if existing is not None and existing.status != "failed":
                return existing
            job = Job(id=job_id)
            self._jobs[job_id] = job
            if job_id in self._order:
                self._order.remove(job_id)
            self._order.append(job_id)

        self._executor.submit(self._run, job, pdf_bytes, run)
        return job

    def _run(self, job: Job, pdf_bytes: bytes, run: Runner) -> None:
        with self._lock:
            if self._jobs.get(job.id) is not job:
                return  # discarded while it was queued
            job.status = "running"

        def on_progress(done: int, total: int) -> None:
            job.pages_done = done
            job.pages_total = total

        try:
            result = run(pdf_bytes, on_progress)
        except Exception as exc:  # malformed PDF, OMR crash, etc.
            job.error = f"Failed to process PDF: {exc}"
            job.status = "failed"
        else:
            job.result = result
            job.status = "done"
        finally:
            del pdf_bytes
            job.finished_at = time.monotonic()

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            self._prune()
            return self._jobs.get(job_id)

    def queue_position(self, job_id: str) -> int:
        """How many jobs are ahead of this one (0 when it's running or done)."""
        with self._lock:
            ahead = 0
            for other_id in self._order:
                if other_id == job_id:
                    return ahead
                if self._jobs[other_id].status in ("queued", "running"):
                    ahead += 1
            return 0

    def discard(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)
            if job_id in self._order:
                self._order.remove(job_id)

    def _prune(self) -> None:
        now = time.monotonic()
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if job.finished_at is not None and now - job.finished_at > RESULT_TTL_SECONDS
        ]
        for job_id in expired:
            del self._jobs[job_id]
            self._order.remove(job_id)


store = JobStore()
