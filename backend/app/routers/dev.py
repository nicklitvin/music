"""Dev-only sample-score endpoint.

Serves a pre-computed sample score (aLIEz, page 0) from the repo-local
`content/full/aliez/` folder so the UI/tracking can be tested against real
note data without re-running the several-minutes-per-page OMR pipeline
every time. See backend/scripts/extract_notes.py for how the notes JSON is
produced, and backend/scripts/run_benchmarks.py for content/'s layout
(content/full/<piece>/{score.pdf,notes.json,performance.mp3}).

`content/` is gitignored -- it holds real, copyrighted sheet music PDFs,
never committed -- so this route 404s cleanly (not a server error) if that
local data isn't present, e.g. on a fresh clone that hasn't run the script.
The only file paths ever read are the two hardcoded constants below, not
anything caller-supplied, so there's no path-traversal surface here.
"""

import base64
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.models import NoteBoundingBox, ProcessScoreResponse, ScorePage
from app.services import omr

router = APIRouter()

CONTENT_DIR = Path(__file__).resolve().parents[3] / "content"
SAMPLE_PDF = CONTENT_DIR / "full" / "aliez" / "score.pdf"
SAMPLE_NOTES = CONTENT_DIR / "full" / "aliez" / "notes.json"
SAMPLE_SCORE_ID = "sample-aliez"


@router.get("/api/dev/sample-score", response_model=ProcessScoreResponse)
async def sample_score(page: int = 0) -> ProcessScoreResponse:
    if not SAMPLE_PDF.exists() or not SAMPLE_NOTES.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                "No local sample data. Expected content/full/aliez/score.pdf and "
                "content/full/aliez/notes.json -- run "
                "`scripts/extract_notes.py ../content/full/aliez/score.pdf --page 0 "
                "--out ../content/full/aliez/notes.json` first."
            ),
        )

    rendered = omr.render_pages(SAMPLE_PDF.read_bytes())
    matching = [p for p in rendered if p[0] == page]
    if not matching:
        raise HTTPException(status_code=404, detail=f"Sample PDF has {len(rendered)} page(s); page {page} doesn't exist")
    index, png_bytes, width, height = matching[0]

    all_notes = json.loads(SAMPLE_NOTES.read_text(encoding="utf-8"))
    page_notes = [NoteBoundingBox(**note) for note in all_notes if note["pageIndex"] == page]

    return ProcessScoreResponse(
        scoreId=SAMPLE_SCORE_ID,
        musicXml='<?xml version="1.0" encoding="UTF-8"?>\n<score-partwise version="4.0" />\n',
        boundingBoxes=page_notes,
        pages=[
            ScorePage(
                pageIndex=index,
                imageBase64=base64.b64encode(png_bytes).decode("ascii"),
                width=width,
                height=height,
            )
        ],
    )
