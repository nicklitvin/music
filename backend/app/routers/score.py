from fastapi import APIRouter, File, Form, HTTPException, UploadFile

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
        result = omr.process_pdf(pdf_bytes)
    except Exception as exc:  # malformed PDF, etc.
        raise HTTPException(status_code=422, detail=f"Failed to process PDF: {exc}") from exc
    finally:
        # Zero-server-storage: nothing was ever written to disk, and the only
        # in-memory copy (pdf_bytes / result) goes out of scope when this
        # request handler returns.
        del pdf_bytes

    return ProcessScoreResponse(
        scoreId=scoreId,
        musicXml=result.music_xml,
        boundingBoxes=result.bounding_boxes,
        pages=result.pages,
    )
