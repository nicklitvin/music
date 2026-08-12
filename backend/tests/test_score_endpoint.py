def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_process_score_returns_pages_and_musicxml(client, sample_pdf_bytes):
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
    assert len(body["boundingBoxes"]) == 1


def test_process_score_rejects_non_pdf(client):
    res = client.post(
        "/api/process-score",
        files={"file": ("test.txt", b"not a pdf", "text/plain")},
        data={"scoreId": "abc123"},
    )
    assert res.status_code == 400
