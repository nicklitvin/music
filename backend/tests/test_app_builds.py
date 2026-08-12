"""Smoke test standing in for a 'does the app build' check.

Python has no separate compile/build step, so the equivalent failure mode is
an import-time error: a bad import, a route wired up wrong, a missing
dependency. Importing app.main and constructing the FastAPI app exercises
all of that.
"""

from app.main import app


def test_app_imports_and_has_expected_routes():
    paths = {route.path for route in app.routes}
    assert "/health" in paths
    assert "/api/process-score" in paths
    assert "/ws/track-audio" in paths
