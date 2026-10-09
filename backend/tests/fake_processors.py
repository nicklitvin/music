"""Stand-ins for OMR that run inside a job's real worker process.

They have to be module-level functions in an importable module: the job
store pickles them by reference to hand them to a spawned worker, so pytest's
monkeypatch (which only affects the test process) can't reach in there.
"""

import logging
import os
import signal
import time

from app.models import NoteBoundingBox


def fake_extract_page(png_bytes, page_index, page_width, page_height):
    box = NoteBoundingBox(
        x=10, y=20, width=5, height=5, note="quarter", pitch="E4", measureIndex=1, pageIndex=page_index
    )
    xml = f'<?xml version="1.0"?>\n<score-partwise version="4.0"><!-- page {page_index} --></score-partwise>'
    return [box], xml


def fake_omr(pdf_bytes, on_progress):
    """The real pipeline (rendering, progress, assembly) minus oemer itself."""
    from app.services import oemer_engine, omr

    oemer_engine.extract_page = fake_extract_page
    return omr.process_pdf(pdf_bytes, on_progress)


def slow_omr(pdf_bytes, on_progress):
    # Says what it's doing the way oemer does, then takes its time.
    logging.getLogger("oemer.ete").info("Extracting noteheads")
    time.sleep(2)
    return fake_omr(pdf_bytes, on_progress)


def endless_omr(pdf_bytes, on_progress):
    time.sleep(120)
    return fake_omr(pdf_bytes, on_progress)


def broken_omr(pdf_bytes, on_progress):
    raise RuntimeError("boom")


def oom_killed_omr(pdf_bytes, on_progress):
    # What the kernel's OOM killer does to a worker.
    os.kill(os.getpid(), getattr(signal, "SIGKILL", signal.SIGTERM))
