import io
import json
import re
from pathlib import Path

import fitz
import pytest

from app.services import jobs, oemer_engine, omr, omr_remote
from app.models import NoteBoundingBox

BACKEND = Path(__file__).resolve().parent.parent


def _box(page_index: int) -> NoteBoundingBox:
    return NoteBoundingBox(
        x=1, y=2, width=3, height=4, note="C4", pitch="C4", measureIndex=0, pageIndex=page_index
    )


class LocalLambda:
    """A Lambda client that runs the handler in-process, through the same
    JSON round trip the real one does."""

    def __init__(self):
        self.calls = []

    def invoke(self, FunctionName, InvocationType, Payload):
        self.calls.append(FunctionName)
        try:
            body = omr_remote.handler(json.loads(Payload))
            return {"StatusCode": 200, "Payload": io.BytesIO(json.dumps(body).encode())}
        except Exception as error:
            body = {"errorType": type(error).__name__, "errorMessage": str(error)}
            return {
                "StatusCode": 200,
                "FunctionError": "Unhandled",
                "Payload": io.BytesIO(json.dumps(body).encode()),
            }


@pytest.fixture
def fake_oemer(monkeypatch):
    seen = []

    def extract_page(png_bytes, page_index, width, height):
        seen.append((png_bytes, page_index, width, height))
        return [_box(page_index)], f"<score page='{page_index}'/>"

    monkeypatch.setattr(oemer_engine, "extract_page", extract_page)
    return seen


def test_a_page_round_trips_through_the_handler(fake_oemer, monkeypatch):
    monkeypatch.setattr(omr_remote.settings, "omr_lambda_function", "music-omr")
    client = LocalLambda()

    index, boxes, xml = omr_remote.extract_remote((2, b"\x89PNG page", 100, 200), client)

    assert fake_oemer == [(b"\x89PNG page", 2, 100, 200)]
    assert client.calls == ["music-omr"]
    assert (index, xml) == (2, "<score page='2'/>")
    assert [NoteBoundingBox(**b) for b in boxes] == [_box(2)]


def test_a_failed_page_says_which_page_and_why(monkeypatch):
    def broken(*args):
        raise IndexError("list index out of range")

    monkeypatch.setattr(oemer_engine, "extract_page", broken)
    with pytest.raises(RuntimeError, match=r"page 4: IndexError: list index out of range"):
        omr_remote.extract_remote((3, b"png", 1, 1), LocalLambda())


def test_an_oversized_page_is_refused_before_sending():
    client = LocalLambda()
    with pytest.raises(ValueError, match="too large"):
        omr_remote.extract_remote((0, b"x" * (5 * 1024 * 1024), 1, 1), client)
    assert client.calls == []


def test_process_pdf_sends_every_page_to_lambda_when_configured(fake_oemer, monkeypatch):
    monkeypatch.setattr(omr.settings, "omr_lambda_function", "music-omr")
    monkeypatch.setattr(omr.settings, "render_dpi", 20)
    client = LocalLambda()
    monkeypatch.setattr(omr_remote, "_lambda_client", lambda: client)
    monkeypatch.setattr(omr, "_extract_one", lambda *a: pytest.fail("ran OMR locally"))

    doc = fitz.open()
    for _ in range(3):
        doc.new_page()
    progress = []
    result = omr.process_pdf(doc.tobytes(), lambda done, total: progress.append((done, total)))

    assert len(client.calls) == 3
    assert [box.pageIndex for box in result.bounding_boxes] == [0, 1, 2]
    assert progress == [(0, 3), (1, 3), (2, 3), (3, 3)]
    assert "<!-- page 2 -->" in result.music_xml


def test_a_waiting_worker_is_not_stalled_when_omr_runs_on_lambda(monkeypatch):
    monkeypatch.setattr(jobs.settings, "omr_lambda_function", "music-omr")
    cpu = iter([10.0, 10.1, 10.2])
    monkeypatch.setattr(jobs, "_cpu_seconds", lambda pid: next(cpu))
    job = jobs.Job(id="remote", status="running", worker_pid=1)

    for now in (0.0, 60.0, 130.0):
        jobs.store._sample(job, now)
    assert not job.stalled


def _pins(path: Path) -> dict[str, str]:
    pins = {}
    for line in path.read_text().splitlines():
        match = re.match(r"^([A-Za-z0-9_.\-\[\]]+)==(\S+)", line.strip())
        if match:
            pins[match.group(1).lower()] = match.group(2)
    return pins


def test_the_lambda_runs_the_same_library_versions_as_the_server():
    server = _pins(BACKEND / "requirements.txt")
    lam = _pins(BACKEND / "lambda" / "requirements.txt")

    assert lam.pop("onnxruntime") == server["onnxruntime-gpu"]
    for name, version in lam.items():
        assert server.get(name) == version, name
    oemer_pin = re.search(r"oemer==(\S+)", (BACKEND / "lambda" / "Dockerfile").read_text())
    assert oemer_pin and oemer_pin.group(1) == server["oemer"]
