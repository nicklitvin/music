"""Optical Music Recognition pipeline.

Page rendering (PDF -> PNG, in-memory only) is via PyMuPDF. Note-level
recognition (MusicXML + per-note bounding boxes) runs the real `oemer` OMR
model per page -- see oemer_engine.py's docstring for how pitch and bounding
boxes are extracted from it. This is CPU-bound and slow (multiple minutes
per page on a machine with no GPU) -- `process_pdf` is meant to be run off
the event loop (see the /api/process-score route, which offloads it to a
thread pool).
"""

import base64
import os
import sys
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass

import fitz  # PyMuPDF

from app.config import settings
from app.models import NoteBoundingBox, ScorePage
from app.services import oemer_engine


@dataclass
class OMRResult:
    music_xml: str
    bounding_boxes: list[NoteBoundingBox]
    pages: list[ScorePage]


def render_pages(pdf_bytes: bytes) -> list[tuple[int, bytes, int, int]]:
    """Render each PDF page to a PNG entirely in memory. Returns (index, png_bytes, width, height)."""
    zoom = settings.render_dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    pages: list[tuple[int, bytes, int, int]] = []

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for index, page in enumerate(doc):
            pixmap = page.get_pixmap(matrix=matrix)
            pages.append((index, pixmap.tobytes("png"), pixmap.width, pixmap.height))

    return pages


def _combine_music_xml(per_page_xml: list[str]) -> str:
    # Each page is independently transcribed by oemer as its own complete
    # MusicXML document (own measure numbering, own <part-list>) -- there's
    # no per-note consumer of this field in the app today (it's stored and
    # passed through only), so pages are concatenated with a clear marker
    # rather than merged into one continuous part, which would need
    # renumbering measures and merging parts across pages.
    if len(per_page_xml) == 1:
        return per_page_xml[0]
    return "\n".join(f"<!-- page {i} -->\n{xml}" for i, xml in enumerate(per_page_xml))


def _extract_one(args: tuple[int, bytes, int, int]) -> tuple[int, list[dict], str]:
    """Worker entry point: must be module level and picklable."""
    index, png_bytes, width, height = args
    boxes, page_xml = oemer_engine.extract_page(png_bytes, index, width, height)
    return index, [box.model_dump() for box in boxes], page_xml


def available_memory_gb() -> float | None:
    """Free physical memory, or None if it can't be determined here."""
    try:
        if sys.platform == "win32":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return status.ullAvailPhys / 1024**3
        with open("/proc/meminfo") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024**2
    except Exception:
        return None
    return None


# Rough peak resident size of one oemer page run. It is the binding
# constraint on parallelism -- see _worker_count.
OEMER_MEMORY_GB = 1.5


def _worker_count(page_count: int) -> int:
    """How many pages to OMR at once.

    Pages are independent, so this is embarrassingly parallel -- but oemer
    is memory-hungry rather than core-hungry, and overcommitting gets the
    whole run OOM-killed partway through (which has happened on this
    machine, with a desktop busy running other things). So the worker count
    is bounded by free memory as well as cores, and a loaded machine
    correctly falls back to processing one page at a time rather than
    failing.
    """
    if settings.omr_workers > 0:
        return max(1, min(settings.omr_workers, page_count))

    limit = min(4, (os.cpu_count() or 2) // 3, page_count)
    free = available_memory_gb()
    if free is not None:
        # Leave a gigabyte for everything else on the machine.
        limit = min(limit, int((free - 1.0) // OEMER_MEMORY_GB))
    return max(1, limit)


def process_pdf(
    pdf_bytes: bytes,
    on_progress: Callable[[int, int], None] | None = None,
) -> OMRResult:
    """Run OMR over every page. `on_progress(pages_done, pages_total)` is
    called once the page count is known and again as each page finishes."""
    rendered = render_pages(pdf_bytes)

    def report(done: int) -> None:
        if on_progress is not None:
            on_progress(done, len(rendered))

    report(0)

    pages = [
        ScorePage(
            pageIndex=index,
            imageBase64=base64.b64encode(png_bytes).decode("ascii"),
            width=width,
            height=height,
        )
        for index, png_bytes, width, height in rendered
    ]

    workers = _worker_count(len(rendered))
    results: dict[int, tuple[list[NoteBoundingBox], str]] = {}
    if workers == 1:
        for item in rendered:
            index, raw_boxes, page_xml = _extract_one(item)
            results[index] = ([NoteBoundingBox(**b) for b in raw_boxes], page_xml)
            report(len(results))
    else:
        # A process pool, not threads: oemer's work is CPU-bound Python
        # (its rule-based passes dominate -- the neural nets are a minority
        # of the time), so threads would serialise on the GIL. Separate
        # processes also hand oemer's memory back to the OS between pages
        # and sidestep its process-global `layers` registry entirely.
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_extract_one, item) for item in rendered]
            for future in as_completed(futures):
                index, raw_boxes, page_xml = future.result()
                results[index] = ([NoteBoundingBox(**b) for b in raw_boxes], page_xml)
                report(len(results))

    bounding_boxes: list[NoteBoundingBox] = []
    per_page_xml: list[str] = []
    for index, _, _, _ in rendered:
        boxes, page_xml = results[index]
        bounding_boxes.extend(boxes)
        per_page_xml.append(page_xml)

    return OMRResult(
        music_xml=_combine_music_xml(per_page_xml),
        bounding_boxes=bounding_boxes,
        pages=pages,
    )
