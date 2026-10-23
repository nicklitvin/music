"""Background OMR jobs, each run in its own worker process.

OMR takes minutes per page, so a whole sheet can run for an hour. Doing that
inside one HTTP request didn't work: the reverse proxy times the request out
long before it finishes, and any backend restart (every deploy) kills it
with nothing to show for it. Instead the upload starts a job here and
returns at once; the client polls for progress and fetches the result when
it's ready.

Each job's OMR runs in a separate worker process rather than a thread of the
web server, for three reasons:

- When oemer runs out of memory, the kernel's OOM killer takes out the
  worker, not the whole backend -- and the job fails saying so, instead of
  sitting at "running" forever.
- The job can report whether the worker is genuinely busy: its stage (from
  oemer's own log lines) and how much CPU it is getting. A worker that is
  alive but starved -- the machine swapping itself to a standstill -- shows
  up as stalled rather than looking like slow progress.
- oemer's models and buffers are handed back to the OS when the worker
  exits, rather than staying resident in the web server between jobs.

Zero-server-storage still holds: nothing here touches disk. A job's PDF
bytes go to the worker over a pipe and are dropped once OMR has run, and the
result lives only in this process's memory until the client collects it
(DELETE) or it expires. A restart loses every job, which the client handles
by resubmitting the PDF it keeps in IndexedDB.
"""

import logging
import math
import multiprocessing
import os
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Literal

from app.config import settings
from app.services import omr_log

log = omr_log.logger

JobState = Literal["queued", "running", "done", "failed"]

# How long a finished job's result is kept for a client that hasn't come
# back for it -- long enough to cover a phone that was locked for a while,
# short enough that abandoned results don't pile up in memory.
RESULT_TTL_SECONDS = 6 * 60 * 60

# A healthy oemer worker keeps a core close to fully busy. Over this window,
# getting less than this share of one core means it is starved -- in
# practice, the machine swapping because it hasn't the memory for oemer.
STALL_WINDOW_SECONDS = 120
STALL_CPU_SHARE = 0.1

# Below this much free memory, a stall is almost certainly memory pressure.
LOW_MEMORY_GB = 0.2

# The worker is stopped once free memory falls below this. Waiting for the
# kernel's OOM killer is too late: by then the machine has been swapping for
# minutes, and nginx and the API stall along with everything else -- the
# whole site goes unreachable (seen on a 4 GB box, a worker at 3.7 GB
# resident plus 1.8 GB of swap). Stopping early fails one upload instead.
MIN_FREE_MEMORY_GB = 0.3

# How much lower the worker's CPU priority is than the web server's, so the
# site stays responsive while a sheet is being processed.
WORKER_NICENESS = 10

# How often a running job logs how it's getting on (see omr_log).
HEARTBEAT_SECONDS = 60

# How long a page takes, for the time-left estimate. Both measured from the
# per-page OMR log: on the server one page at a time (~190s on two cores);
# on Lambda (3008 MB) 187s for each page of a 4-page sheet, all of which ran
# at once -- so a sheet takes as long as its slowest page, which is where
# the margin comes in (one dense page took 249s).
SECONDS_PER_PAGE = 195
LAMBDA_SECONDS_PER_PAGE = 220

# A worker killed by signal 9 is, on a box with no one else killing things,
# the kernel's OOM killer.
_KILLED = (-9, 137)


@dataclass
class Job:
    id: str
    status: JobState = "queued"
    pages_done: int = 0
    pages_total: int | None = None
    stage: str | None = None
    error: str | None = None
    result: Any = None
    worker_pid: int | None = None
    cpu_seconds: float | None = None
    stalled: bool = False
    low_memory: bool = False
    created_at: float = field(default_factory=time.monotonic)
    started_at: float | None = None
    finished_at: float | None = None
    # When the pages were rendered and recognition began, and when the last
    # page finished: what the time-left estimate counts from.
    pages_started_at: float | None = None
    last_page_at: float | None = None
    # (monotonic time, worker CPU seconds) samples for stall detection.
    cpu_samples: list[tuple[float, float]] = field(default_factory=list)


# Runs in the worker process. Must be a module-level function (it's pickled
# by reference to reach the worker) taking the PDF bytes and a progress
# callback `(pages_done, pages_total)`.
Processor = Callable[[bytes, Callable[[int, int], None]], Any]


class _StageHandler(logging.Handler):
    """Forwards oemer's own progress log lines ("Extracting noteheads"...)."""

    def __init__(self, send: Callable[[tuple], None]) -> None:
        super().__init__(level=logging.INFO)
        self._send = send

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._send(("stage", record.getMessage()))
        except Exception:
            pass


def seconds_left(job: "Job", now: float | None = None) -> int | None:
    """Roughly how long until `job` is done, or None before that can be
    said (still queued, or the PDF not yet rendered)."""
    if job.status != "running" or not job.pages_total or job.pages_started_at is None:
        return None
    now = time.monotonic() if now is None else now
    if settings.omr_lambda_function:
        # Every page at once, up to the concurrency limit; past that, the
        # rest wait for a slot.
        rounds = math.ceil(job.pages_total / max(1, settings.omr_lambda_concurrency))
        left = rounds * LAMBDA_SECONDS_PER_PAGE - (now - job.pages_started_at)
    else:
        pages_after_this = max(0, job.pages_total - job.pages_done - 1)
        this_page = SECONDS_PER_PAGE - (now - (job.last_page_at or job.pages_started_at))
        left = pages_after_this * SECONDS_PER_PAGE + max(0, this_page)
    return max(0, round(left))


def _short(job_id: str) -> str:
    return job_id[:8]


def _duration(seconds: float) -> str:
    seconds = round(seconds)
    return f"{seconds // 60}m{seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


def _worker_main(conn, process: Processor, pdf_bytes: bytes) -> None:
    """Entry point of the worker process."""
    send_lock = threading.Lock()
    omr_log.configure()
    try:
        os.nice(WORKER_NICENESS)
    except (AttributeError, OSError):
        pass  # not on Windows

    def send(message: tuple) -> None:
        with send_lock:
            conn.send(message)

    # oemer logs each stage at INFO on loggers under "oemer"; make sure
    # they get through regardless of how logging is configured here.
    oemer_logger = logging.getLogger("oemer")
    oemer_logger.addHandler(_StageHandler(send))
    oemer_logger.setLevel(logging.INFO)
    send(("stage", "Reading the PDF"))
    try:
        result = process(pdf_bytes, lambda done, total: send(("progress", done, total)))
    except Exception as exc:  # malformed PDF, OMR crash, etc.
        send(("error", f"Failed to process PDF: {exc}"))
    else:
        send(("done", result))
    finally:
        conn.close()


def _cpu_seconds(pid: int) -> float | None:
    """CPU time used by a process and its descendants (Linux only)."""
    if not sys.platform.startswith("linux"):
        return None
    tick = os.sysconf("SC_CLK_TCK")
    total = 0.0
    pending = [pid]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        try:
            with open(f"/proc/{current}/stat") as handle:
                # The command name (field 2) can contain spaces; everything
                # after its closing paren is space-separated.
                fields = handle.read().rsplit(")", 1)[1].split()
            total += (int(fields[11]) + int(fields[12])) / tick
            for task in os.listdir(f"/proc/{current}/task"):
                with open(f"/proc/{current}/task/{task}/children") as handle:
                    pending.extend(int(child) for child in handle.read().split())
        except (OSError, IndexError, ValueError):
            continue
    return total


def _free_memory_gb() -> float | None:
    from app.services.omr import available_memory_gb

    return available_memory_gb()


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
        # spawn, not fork: the web server has threads (event loop, this
        # executor), and forking a threaded process can copy a held lock
        # into the child and deadlock it.
        self._mp = multiprocessing.get_context("spawn")

    def submit(self, job_id: str, pdf_bytes: bytes, process: Processor) -> Job:
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

        log.info(
            "job %s queued: %.1f MB PDF, %d job(s) ahead",
            _short(job_id),
            len(pdf_bytes) / 1024**2,
            self.queue_position(job_id),
        )
        self._executor.submit(self._run, job, pdf_bytes, process)
        return job

    def _current(self, job: Job) -> bool:
        with self._lock:
            return self._jobs.get(job.id) is job

    def _run(self, job: Job, pdf_bytes: bytes, process: Processor) -> None:
        if not self._current(job):
            return  # discarded while it was queued

        receiver, sender = self._mp.Pipe(duplex=False)
        worker = self._mp.Process(target=_worker_main, args=(sender, process, pdf_bytes), daemon=True)
        del pdf_bytes
        try:
            worker.start()
        except Exception as exc:
            self._finish(job, error=f"Couldn't start the OMR worker: {exc}")
            return
        sender.close()  # so the pipe reports EOF if the worker dies

        job.worker_pid = worker.pid
        job.started_at = time.monotonic()
        job.stage = "Starting the worker"
        job.status = "running"
        log.info(
            "job %s started after %s queued, worker pid %s",
            _short(job.id),
            _duration(job.started_at - job.created_at),
            worker.pid,
        )

        outcome: tuple | None = None
        last_sample = 0.0
        machine = omr_log.MachineSampler()
        last_heartbeat = last_page = job.started_at
        heartbeat_cpu = 0.0
        try:
            while outcome is None:
                if not self._current(job):
                    # Cancelled: actually stop the work, rather than letting
                    # it grind on for nobody.
                    worker.kill()
                    log.info("job %s cancelled, worker stopped", _short(job.id))
                    return
                try:
                    if receiver.poll(1.0):
                        message = receiver.recv()
                        kind = message[0]
                        if kind == "stage":
                            job.stage = message[1]
                        elif kind == "progress":
                            job.pages_done, job.pages_total = message[1], message[2]
                            now = time.monotonic()
                            if job.pages_done == 0:
                                job.pages_started_at = now
                                log.info("job %s: %s page(s)", _short(job.id), job.pages_total)
                            else:
                                log.info(
                                    "job %s: page %d/%d done, %s since the last",
                                    _short(job.id),
                                    job.pages_done,
                                    job.pages_total,
                                    _duration(now - last_page),
                                )
                            last_page = job.last_page_at = now
                        else:
                            outcome = message
                except (EOFError, OSError):
                    break  # the worker is gone without saying why

                free = _free_memory_gb()
                if free is not None and free < MIN_FREE_MEMORY_GB:
                    worker.kill()
                    log.warning(
                        "job %s: free memory down to %.2f GB, stopping the worker (%s)",
                        _short(job.id),
                        free,
                        machine.sample(),
                    )
                    self._finish(
                        job,
                        error=(
                            "Stopped: the server ran out of memory processing this sheet, "
                            "and was about to become unresponsive"
                        ),
                    )
                    return

                now = time.monotonic()
                if now - last_sample >= 5:
                    last_sample = now
                    was_stalled = job.stalled
                    self._sample(job, now)
                    if job.stalled and not was_stalled:
                        log.warning(
                            "job %s stalled: under %.0f%% of a core for %ds (%s)",
                            _short(job.id),
                            STALL_CPU_SHARE * 100,
                            STALL_WINDOW_SECONDS,
                            machine.sample(),
                        )
                if now - last_heartbeat >= HEARTBEAT_SECONDS:
                    cpu = job.cpu_seconds or 0.0
                    memory = omr_log.process_memory_gb(worker.pid)
                    log.info(
                        "job %s running %s: page %d/%s, %r | worker using %.1f cores%s | %s",
                        _short(job.id),
                        _duration(now - job.started_at),
                        job.pages_done + 1,
                        job.pages_total or "?",
                        job.stage,
                        (cpu - heartbeat_cpu) / (now - last_heartbeat),
                        f", {memory[0]:.1f} GB (peak {memory[1]:.1f})" if memory else "",
                        machine.sample(),
                    )
                    last_heartbeat, heartbeat_cpu = now, cpu
        finally:
            receiver.close()
            worker.join(timeout=10)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=5)

        if outcome is None:
            if worker.exitcode in _KILLED:
                error = (
                    "The OMR worker was killed, almost certainly because the server ran out of memory "
                    "(it needs roughly 1.5 GB per page)"
                )
            else:
                error = f"The OMR worker exited unexpectedly (exit code {worker.exitcode})"
            self._finish(job, error=error)
        elif outcome[0] == "done":
            self._finish(job, result=outcome[1])
        else:
            self._finish(job, error=outcome[1])

    def _sample(self, job: Job, now: float) -> None:
        if job.worker_pid is None:
            return
        cpu = _cpu_seconds(job.worker_pid)
        if cpu is None:
            return  # can't measure here (not Linux)
        job.cpu_seconds = cpu
        job.cpu_samples.append((now, cpu))
        # Keep just enough history to look back over the window.
        while len(job.cpu_samples) > 2 and now - job.cpu_samples[1][0] >= STALL_WINDOW_SECONDS:
            job.cpu_samples.pop(0)
        start_time, start_cpu = job.cpu_samples[0]
        elapsed = now - start_time
        # With OMR on Lambda the worker only waits, so low CPU is normal.
        if elapsed >= STALL_WINDOW_SECONDS and not settings.omr_lambda_function:
            job.stalled = (cpu - start_cpu) / elapsed < STALL_CPU_SHARE
            free = _free_memory_gb()
            job.low_memory = free is not None and free < LOW_MEMORY_GB

    def _finish(self, job: Job, *, result: Any = None, error: str | None = None) -> None:
        job.result = result
        job.error = error
        job.stalled = False
        job.stage = None
        job.finished_at = time.monotonic()
        job.status = "failed" if error else "done"
        seconds = job.finished_at - (job.started_at or job.created_at)
        if error:
            log.warning("job %s failed after %s: %s", _short(job.id), _duration(seconds), error)
        else:
            pages = job.pages_total or 0
            log.info(
                "job %s done in %s: %d page(s), %s a page",
                _short(job.id),
                _duration(seconds),
                pages,
                _duration(seconds / max(1, pages)),
            )

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
        """Forget a job; a running one's worker is killed within a second."""
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
