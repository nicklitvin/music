"""Optical Music Recognition pipeline.

Page rendering (PDF -> PNG, in-memory only) is real, via PyMuPDF. Note-level
recognition (MusicXML + bounding boxes) is a stub pending integration with a
real OMR engine (e.g. Audiveris run out-of-process, or an OMR API). The stub
still returns a well-formed, minimal MusicXML document and one placeholder
bounding box per page so the frontend pipeline (IndexedDB save, viewer
overlay, WebSocket note matching) can be built and tested end-to-end before
real recognition is wired in.
"""

import base64
from dataclasses import dataclass

import fitz  # PyMuPDF

from app.config import settings
from app.models import NoteBoundingBox, ScorePage


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


def _stub_music_xml(page_count: int) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<score-partwise version="4.0">\n'
        "  <part-list>\n"
        '    <score-part id="P1"><part-name>Piano</part-name></score-part>\n'
        "  </part-list>\n"
        '  <part id="P1">\n'
        f"    <!-- TODO: replace with real OMR output for {page_count} page(s) -->\n"
        "  </part>\n"
        "</score-partwise>\n"
    )


def process_pdf(pdf_bytes: bytes) -> OMRResult:
    rendered = render_pages(pdf_bytes)

    pages = [
        ScorePage(
            pageIndex=index,
            imageBase64=base64.b64encode(png_bytes).decode("ascii"),
            width=width,
            height=height,
        )
        for index, png_bytes, width, height in rendered
    ]

    # Placeholder bounding box per page until real OMR note extraction lands.
    bounding_boxes = [
        NoteBoundingBox(
            x=page.width * 0.1,
            y=page.height * 0.1,
            width=page.width * 0.05,
            height=page.height * 0.03,
            note="quarter",
            pitch="C4",
            measureIndex=0,
            pageIndex=page.pageIndex,
        )
        for page in pages
    ]

    return OMRResult(
        music_xml=_stub_music_xml(len(pages)),
        bounding_boxes=bounding_boxes,
        pages=pages,
    )
