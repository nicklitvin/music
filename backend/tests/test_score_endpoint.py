import sys
import time

import pytest

from app.routers import score
from tests import fake_processors


@pytest.fixture
def processor(monkeypatch):
    def use(fn):
        monkeypatch.setattr(score, "PROCESSOR", fn)

    use(fake_processors.fake_omr)
    return use


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def _wait_for_job(client, job_id, until=("done", "failed"), timeout=60.0):
    deadline = time.monotonic() + timeout
    body = None
    while time.monotonic() < deadline:
        res = client.get(f"/api/process-score/{job_id}")
        assert res.status_code == 200
        body = res.json()
        if until(body) if callable(until) else body["status"] in until:
            return body
        time.sleep(0.05)
    raise AssertionError(f"job never got there; last status {body}")


def _upload(client, pdf_bytes, score_id):
    return client.post(
        "/api/process-score",
        files={"file": ("test.pdf", pdf_bytes, "application/pdf")},
        data={"scoreId": score_id},
    )


def test_process_score_runs_as_a_job_and_returns_pages_and_musicxml(client, sample_pdf_bytes, processor):
    # Upload returns straight away rather than holding the request open for
    # the whole of OMR, which no proxy would allow for a long sheet.
    res = _upload(client, sample_pdf_bytes, "abc123")
    assert res.status_code == 202
    assert res.json()["jobId"] == "abc123"

    status = _wait_for_job(client, "abc123")
    assert status["status"] == "done"
    assert status["pagesDone"] == 1
    assert status["pagesTotal"] == 1

    res = client.get("/api/process-score/abc123/result")
    assert res.status_code == 200
    body = res.json()
    assert body["scoreId"] == "abc123"
    assert "score-partwise" in body["musicXml"]
    assert len(body["pages"]) == 1
    assert body["pages"][0]["pageIndex"] == 0
    assert body["boundingBoxes"] == [
        {"x": 10, "y": 20, "width": 5, "height": 5, "note": "quarter", "pitch": "E4", "measureIndex": 1, "pageIndex": 0}
    ]

    # Collecting the result frees it; after that the job is gone.
    assert client.delete("/api/process-score/abc123").status_code == 204
    assert client.get("/api/process-score/abc123").status_code == 404


def test_the_worker_reports_what_it_is_doing(client, sample_pdf_bytes, processor):
    # So "processing" can be told apart from "stuck": the stage comes from
    # oemer's own log lines, relayed out of the worker process.
    processor(fake_processors.slow_omr)
    _upload(client, sample_pdf_bytes, "stages")

    status = _wait_for_job(client, "stages", until=lambda b: b["stage"] == "Extracting noteheads")
    assert status["status"] == "running"
    assert _wait_for_job(client, "stages")["status"] == "done"
    client.delete("/api/process-score/stages")


def test_resubmitting_the_same_score_reuses_the_running_job(client, sample_pdf_bytes, processor):
    # A client that refreshed mid-upload doesn't know whether its upload
    # landed, so it submits again -- that mustn't start OMR a second time.
    from app.services import jobs

    processor(fake_processors.slow_omr)
    assert _upload(client, sample_pdf_bytes, "same").status_code == 202
    first = jobs.store.get("same")
    assert _upload(client, sample_pdf_bytes, "same").status_code == 202
    assert jobs.store.get("same") is first

    assert _wait_for_job(client, "same")["status"] == "done"
    client.delete("/api/process-score/same")


def test_a_failed_job_reports_its_error_and_can_be_retried(client, sample_pdf_bytes, processor):
    processor(fake_processors.broken_omr)
    _upload(client, sample_pdf_bytes, "flaky")
    status = _wait_for_job(client, "flaky")
    assert status["status"] == "failed"
    assert "boom" in status["error"]
    assert client.get("/api/process-score/flaky/result").status_code == 409

    processor(fake_processors.fake_omr)
    _upload(client, sample_pdf_bytes, "flaky")
    assert _wait_for_job(client, "flaky")["status"] == "done"
    client.delete("/api/process-score/flaky")


def test_a_worker_killed_mid_job_fails_the_job_instead_of_hanging(client, sample_pdf_bytes, processor):
    # Before the worker was its own process, an OOM kill took the whole
    # backend with it, and a starved run sat at "running" indefinitely.
    processor(fake_processors.oom_killed_omr)
    _upload(client, sample_pdf_bytes, "killed")

    status = _wait_for_job(client, "killed")
    assert status["status"] == "failed"
    assert "worker" in status["error"]
    if sys.platform.startswith("linux"):
        assert "out of memory" in status["error"]
    client.delete("/api/process-score/killed")


def test_a_worker_is_stopped_before_it_runs_the_server_out_of_memory(
    client, sample_pdf_bytes, processor, monkeypatch
):
    # Left to the kernel's OOM killer, the machine swaps for minutes first
    # and the whole site goes unreachable. Stopping at a free-memory floor
    # fails one upload instead.
    from app.services import jobs

    processor(fake_processors.endless_omr)
    monkeypatch.setattr(jobs, "_free_memory_gb", lambda: jobs.MIN_FREE_MEMORY_GB / 2)
    _upload(client, sample_pdf_bytes, "hog")

    status = _wait_for_job(client, "hog", timeout=30)
    assert status["status"] == "failed"
    assert "out of memory" in status["error"]
    client.delete("/api/process-score/hog")


def test_cancelling_a_running_job_stops_its_worker(client, sample_pdf_bytes, processor):
    # Jobs run one at a time, so if the cancelled worker kept going, the
    # next sheet would wait behind it for two minutes.
    processor(fake_processors.endless_omr)
    _upload(client, sample_pdf_bytes, "doomed")
    _wait_for_job(client, "doomed", until=("running",))
    client.delete("/api/process-score/doomed")

    processor(fake_processors.fake_omr)
    _upload(client, sample_pdf_bytes, "next")
    assert _wait_for_job(client, "next", timeout=30)["status"] == "done"
    client.delete("/api/process-score/next")


def test_unknown_job_is_404_so_the_client_knows_to_resubmit(client):
    # What a client sees after a backend restart lost its job.
    assert client.get("/api/process-score/never-heard-of-it").status_code == 404
    assert client.get("/api/process-score/never-heard-of-it/result").status_code == 404


def test_process_score_rejects_non_pdf(client):
    res = client.post(
        "/api/process-score",
        files={"file": ("test.txt", b"not a pdf", "text/plain")},
        data={"scoreId": "abc123"},
    )
    assert res.status_code == 400


def test_omr_worker_count_backs_off_when_memory_is_short(monkeypatch):
    # oemer is memory-hungry: overcommitting gets the whole run OOM-killed
    # partway through, so a busy machine must fall back to one page at a
    # time rather than failing.
    from app.services import omr

    monkeypatch.setattr(omr.settings, "omr_workers", 0)
    monkeypatch.setattr(omr, "available_memory_gb", lambda: 2.0)
    assert omr._worker_count(10) == 1

    monkeypatch.setattr(omr, "available_memory_gb", lambda: 32.0)
    assert omr._worker_count(10) > 1

    # Never more workers than there are pages to do.
    assert omr._worker_count(1) == 1


def test_omr_worker_count_is_overridable(monkeypatch):
    from app.services import omr

    monkeypatch.setattr(omr.settings, "omr_workers", 3)
    monkeypatch.setattr(omr, "available_memory_gb", lambda: 2.0)
    assert omr._worker_count(10) == 3


def test_omr_worker_count_survives_an_unknown_memory_figure(monkeypatch):
    from app.services import omr

    monkeypatch.setattr(omr.settings, "omr_workers", 0)
    monkeypatch.setattr(omr, "available_memory_gb", lambda: None)
    assert omr._worker_count(10) >= 1


def test_a_starved_worker_is_flagged_as_stalled_and_blamed_on_memory(monkeypatch):
    # What the 1 GB production box did: the worker alive but swapping, a few
    # percent of a core for twenty minutes, looking just like slow progress.
    from app.routers.score import _warning
    from app.services import jobs

    cpu = iter([10.0, 10.5, 11.0])
    monkeypatch.setattr(jobs, "_cpu_seconds", lambda pid: next(cpu))
    monkeypatch.setattr(jobs, "_free_memory_gb", lambda: 0.05)
    job = jobs.Job(id="starved", status="running", worker_pid=1)

    jobs.store._sample(job, 0.0)
    jobs.store._sample(job, 60.0)
    assert not job.stalled  # not enough history to judge yet
    jobs.store._sample(job, 130.0)
    assert job.stalled
    assert "out of memory" in _warning(job)


def test_a_busy_worker_is_not_flagged(monkeypatch):
    from app.services import jobs

    cpu = iter([10.0, 70.0, 140.0])
    monkeypatch.setattr(jobs, "_cpu_seconds", lambda pid: next(cpu))
    monkeypatch.setattr(jobs, "_free_memory_gb", lambda: 2.0)
    job = jobs.Job(id="busy", status="running", worker_pid=1)

    for now in (0.0, 60.0, 130.0):
        jobs.store._sample(job, now)
    assert not job.stalled


def test_time_left_on_the_server_counts_down_page_by_page(monkeypatch):
    from app.services import jobs

    monkeypatch.setattr(jobs.settings, "omr_lambda_function", "")
    job = jobs.Job(id="local", status="running", pages_total=3, pages_started_at=0.0)

    assert jobs.seconds_left(job, now=0.0) == 3 * jobs.SECONDS_PER_PAGE
    job.pages_done, job.last_page_at = 1, 200.0
    assert jobs.seconds_left(job, now=250.0) == 2 * jobs.SECONDS_PER_PAGE - 50
    # A page running long doesn't eat into the ones after it.
    assert jobs.seconds_left(job, now=1000.0) == jobs.SECONDS_PER_PAGE


def test_time_left_on_lambda_is_one_page_however_long_the_sheet(monkeypatch):
    from app.services import jobs

    monkeypatch.setattr(jobs.settings, "omr_lambda_function", "music-omr")
    monkeypatch.setattr(jobs.settings, "omr_lambda_concurrency", 10)
    one = jobs.LAMBDA_SECONDS_PER_PAGE
    sheet = jobs.Job(id="lambda", status="running", pages_total=8, pages_started_at=100.0)

    assert jobs.seconds_left(sheet, now=100.0) == one
    assert jobs.seconds_left(sheet, now=160.0) == one - 60
    assert jobs.seconds_left(sheet, now=100.0 + 2 * one) == 0
    # Past the concurrency limit, the rest wait for a slot.
    sheet.pages_total = 15
    assert jobs.seconds_left(sheet, now=100.0) == 2 * one


def test_no_time_left_until_the_pages_are_known():
    from app.services import jobs

    assert jobs.seconds_left(jobs.Job(id="q", status="queued")) is None
    assert jobs.seconds_left(jobs.Job(id="r", status="running")) is None
