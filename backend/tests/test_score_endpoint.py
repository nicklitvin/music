import threading
import time

from app.models import NoteBoundingBox
from app.services import oemer_engine


def _fake_extract_page(png_bytes, page_index, page_width, page_height):
    box = NoteBoundingBox(
        x=10, y=20, width=5, height=5, note="quarter", pitch="E4", measureIndex=1, pageIndex=page_index
    )
    xml = f'<?xml version="1.0"?>\n<score-partwise version="4.0"><!-- page {page_index} --></score-partwise>'
    return [box], xml


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def _wait_for_job(client, job_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        res = client.get(f"/api/process-score/{job_id}")
        assert res.status_code == 200
        if res.json()["status"] in ("done", "failed"):
            return res.json()
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def _upload(client, pdf_bytes, score_id):
    return client.post(
        "/api/process-score",
        files={"file": ("test.pdf", pdf_bytes, "application/pdf")},
        data={"scoreId": score_id},
    )


def test_process_score_runs_as_a_job_and_returns_pages_and_musicxml(client, sample_pdf_bytes, monkeypatch):
    # Real OMR (oemer) does minutes-long ML inference per page -- far too
    # slow for the default test suite (this runs on every commit), so the
    # OMR step itself is faked here. Its actual behavior is covered by
    # test_oemer_engine.py's unit tests against the pure bbox-mapping logic.
    monkeypatch.setattr(oemer_engine, "extract_page", _fake_extract_page)

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


def test_resubmitting_the_same_score_reuses_the_running_job(client, sample_pdf_bytes, monkeypatch):
    # A client that refreshed mid-upload doesn't know whether its upload
    # landed, so it submits again -- that mustn't start OMR a second time.
    release = threading.Event()
    calls = []

    def slow_extract(png_bytes, page_index, page_width, page_height):
        calls.append(page_index)
        release.wait(5)
        return _fake_extract_page(png_bytes, page_index, page_width, page_height)

    monkeypatch.setattr(oemer_engine, "extract_page", slow_extract)

    assert _upload(client, sample_pdf_bytes, "same").status_code == 202
    assert _upload(client, sample_pdf_bytes, "same").status_code == 202
    release.set()

    assert _wait_for_job(client, "same")["status"] == "done"
    assert calls == [0]
    client.delete("/api/process-score/same")


def test_a_failed_job_reports_its_error_and_can_be_retried(client, sample_pdf_bytes, monkeypatch):
    def broken(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(oemer_engine, "extract_page", broken)
    _upload(client, sample_pdf_bytes, "flaky")
    status = _wait_for_job(client, "flaky")
    assert status["status"] == "failed"
    assert "boom" in status["error"]
    assert client.get("/api/process-score/flaky/result").status_code == 409

    monkeypatch.setattr(oemer_engine, "extract_page", _fake_extract_page)
    _upload(client, sample_pdf_bytes, "flaky")
    assert _wait_for_job(client, "flaky")["status"] == "done"
    client.delete("/api/process-score/flaky")


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
