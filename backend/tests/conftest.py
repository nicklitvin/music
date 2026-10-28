import io

import fitz
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


@pytest.fixture(autouse=True)
def no_session_logs(monkeypatch):
    """Tests drive plenty of tracking sessions; don't log them into the
    real log folder. Tests of the log itself point it somewhere else."""
    monkeypatch.setattr(settings, "session_log_dir", "")


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def sample_pdf_bytes() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Test Score")
    buffer = io.BytesIO()
    doc.save(buffer)
    doc.close()
    return buffer.getvalue()
