"""Serves the committed accuracy-benchmark results for the frontend's
Benchmarks page.

Unlike content/ (gitignored -- real sheet music and recordings), the
results file this reads is checked into the repo: it holds only aggregate
metrics (F1 scores, tracking accuracy percentages, jump counts, ...), never
any of the underlying musical content. See scripts/run_benchmarks.py for
how it's produced and app/services/benchmark_eval.py for what it measures.
"""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

RESULTS_PATH = Path(__file__).resolve().parents[2] / "benchmark_results.json"


@router.get("/api/benchmarks")
async def benchmarks() -> dict:
    if not RESULTS_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="No benchmark results yet -- run `scripts/run_benchmarks.py` first.",
        )
    return json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
