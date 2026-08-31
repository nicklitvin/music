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


def test_process_score_returns_pages_and_musicxml(client, sample_pdf_bytes, monkeypatch):
    # Real OMR (oemer) does minutes-long ML inference per page -- far too
    # slow for the default test suite (this runs on every commit), so the
    # OMR step itself is faked here. Its actual behavior is covered by
    # test_oemer_engine.py's unit tests against the pure bbox-mapping logic.
    monkeypatch.setattr(oemer_engine, "extract_page", _fake_extract_page)

    res = client.post(
        "/api/process-score",
        files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
        data={"scoreId": "abc123"},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["scoreId"] == "abc123"
    assert "score-partwise" in body["musicXml"]
    assert len(body["pages"]) == 1
    assert body["pages"][0]["pageIndex"] == 0
    assert body["boundingBoxes"] == [
        {"x": 10, "y": 20, "width": 5, "height": 5, "note": "quarter", "pitch": "E4", "measureIndex": 1, "pageIndex": 0}
    ]


def test_process_score_rejects_non_pdf(client):
    res = client.post(
        "/api/process-score",
        files={"file": ("test.txt", b"not a pdf", "text/plain")},
        data={"scoreId": "abc123"},
    )
    assert res.status_code == 400
