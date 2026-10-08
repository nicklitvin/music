from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile

from app.models import ProcessScoreJob, ProcessScoreResponse
from app.services import jobs, omr

router = APIRouter()

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _job_view(job: jobs.Job) -> ProcessScoreJob:
    return ProcessScoreJob(
        jobId=job.id,
        status=job.status,
        pagesDone=job.pages_done,
        pagesTotal=job.pages_total,
        queuePosition=jobs.store.queue_position(job.id) if job.status == "queued" else 0,
        error=job.error,
    )


def _get_job(job_id: str) -> jobs.Job:
    job = jobs.store.get(job_id)
    if job is None:
        # Unknown, expired, or lost to a backend restart -- the client
        # resubmits the PDF it kept.
        raise HTTPException(status_code=404, detail="No such job")
    return job


# OMR runs minutes per page, so a sheet can take an hour -- far longer than
# any proxy will hold a request open, and longer than the backend reliably
# stays up between deploys. So this only starts a background job (keyed by
# the client's scoreId, and idempotent on it) and returns straight away; the
# client polls the job and collects the result. See services/jobs.py.
@router.post("/api/process-score", response_model=ProcessScoreJob, status_code=202)
async def process_score(file: UploadFile = File(...), scoreId: str = Form(...)) -> ProcessScoreJob:
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only application/pdf uploads are supported")

    pdf_bytes = await file.read()
    if len(pdf_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="PDF exceeds maximum upload size")

    def run(data: bytes, on_progress) -> ProcessScoreResponse:
        # Zero-server-storage: the PDF and rendered page images stay in
        # memory and are dropped once the job's result is collected. (OMR
        # does write each page's PNG to a short-lived, auto-deleted temp
        # file during its own processing -- see oemer_engine.py's docstring
        # for why.)
        result = omr.process_pdf(data, on_progress)
        return ProcessScoreResponse(
            scoreId=scoreId,
            musicXml=result.music_xml,
            boundingBoxes=result.bounding_boxes,
            pages=result.pages,
        )

    job = jobs.store.submit(scoreId, pdf_bytes, run)
    del pdf_bytes
    return _job_view(job)


@router.get("/api/process-score/{job_id}", response_model=ProcessScoreJob)
async def get_job(job_id: str) -> ProcessScoreJob:
    return _job_view(_get_job(job_id))


@router.get("/api/process-score/{job_id}/result", response_model=ProcessScoreResponse)
async def get_job_result(job_id: str) -> ProcessScoreResponse:
    job = _get_job(job_id)
    if job.status != "done" or job.result is None:
        raise HTTPException(status_code=409, detail=f"Job is {job.status}")
    return job.result


# The client calls this once it has saved the result (or gives up on the
# upload), so the result doesn't sit in server memory until it expires.
@router.delete("/api/process-score/{job_id}", status_code=204)
async def discard_job(job_id: str) -> Response:
    jobs.store.discard(job_id)
    return Response(status_code=204)
