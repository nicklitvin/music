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

    bounding_boxes: list[NoteBoundingBox] = []
    per_page_xml: list[str] = []
    for index, png_bytes, width, height in rendered:
        boxes, page_xml = oemer_engine.extract_page(png_bytes, index, width, height)
        bounding_boxes.extend(boxes)
        per_page_xml.append(page_xml)

    return OMRResult(
        music_xml=_combine_music_xml(per_page_xml),
        bounding_boxes=bounding_boxes,
        pages=pages,
    )
