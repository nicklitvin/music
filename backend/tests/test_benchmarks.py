import json

from app.routers import benchmarks


def test_benchmarks_returns_404_when_no_results_file(client, monkeypatch, tmp_path):
    # benchmark_results.json is committed, but shouldn't be load-bearing --
    # a fresh clone before anyone has run run_benchmarks.py should 404
    # cleanly, not error.
    monkeypatch.setattr(benchmarks, "RESULTS_PATH", tmp_path / "does-not-exist.json")

    res = client.get("/api/benchmarks")

    assert res.status_code == 404


def test_benchmarks_returns_the_committed_results(client, monkeypatch, tmp_path):
    payload = {"generatedAt": "2026-01-01T00:00:00", "full": [{"slug": "aliez"}]}
    results_path = tmp_path / "benchmark_results.json"
    results_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(benchmarks, "RESULTS_PATH", results_path)

    res = client.get("/api/benchmarks")

    assert res.status_code == 200
    assert res.json() == payload
