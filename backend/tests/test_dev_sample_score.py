from app.routers import dev


def test_sample_score_returns_404_when_local_content_missing(client, monkeypatch):
    # The real content/ dir holds a real, gitignored copyrighted PDF -- not
    # guaranteed to exist (e.g. CI, a fresh clone). This should 404 cleanly
    # rather than error.
    monkeypatch.setattr(dev, "SAMPLE_PDF", dev.CONTENT_DIR / "does-not-exist.pdf")

    res = client.get("/api/dev/sample-score")

    assert res.status_code == 404


def test_sample_score_returns_data_when_local_content_present(client):
    if not dev.SAMPLE_PDF.exists() or not dev.SAMPLE_NOTES.exists():
        return  # No local sample data on this machine -- nothing to check.

    res = client.get("/api/dev/sample-score?page=0")

    assert res.status_code == 200
    body = res.json()
    assert body["scoreId"] == "sample-aliez"
    assert len(body["pages"]) == 1
    assert body["pages"][0]["pageIndex"] == 0
    assert len(body["boundingBoxes"]) > 0
    assert all(box["pageIndex"] == 0 for box in body["boundingBoxes"])


def test_sample_score_404s_for_out_of_range_page(client):
    if not dev.SAMPLE_PDF.exists() or not dev.SAMPLE_NOTES.exists():
        return

    res = client.get("/api/dev/sample-score?page=999")

    assert res.status_code == 404
