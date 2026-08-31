from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from app.models import ProcessScoreResponse
from app.services import omr

router = APIRouter()

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


@router.post("/api/process-score", response_model=ProcessScoreResponse)
async def process_score(file: UploadFile = File(...), scoreId: str = Form(...)) -> ProcessScoreResponse:
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only application/pdf uploads are supported")

    pdf_bytes = await file.read()
    if len(pdf_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="PDF exceeds maximum upload size")

    try:
        # process_pdf is slow (real OMR inference, minutes per page) and
        # synchronous/CPU-bound -- run it off the event loop so it doesn't
        # block other requests (including in-flight audio-tracking
        # WebSockets) for the duration.
        result = await run_in_threadpool(omr.process_pdf, pdf_bytes)
    except Exception as exc:  # malformed PDF, etc.
        raise HTTPException(status_code=422, detail=f"Failed to process PDF: {exc}") from exc
    finally:
        # Zero-server-storage: the uploaded PDF and rendered page images are
        # never written to disk, and this in-memory copy goes out of scope
        # when the request handler returns. (OMR does write each page's PNG
        # to a short-lived, auto-deleted temp file during its own
        # processing -- see oemer_engine.py's docstring for why.)
        del pdf_bytes

    return ProcessScoreResponse(
        scoreId=scoreId,
        musicXml=result.music_xml,
        boundingBoxes=result.bounding_boxes,
        pages=result.pages,
    )
