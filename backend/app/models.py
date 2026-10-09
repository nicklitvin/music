from pydantic import BaseModel


class NoteBoundingBox(BaseModel):
    x: float
    y: float
    width: float
    height: float
    note: str
    pitch: str
    measureIndex: int
    pageIndex: int


class ScorePage(BaseModel):
    pageIndex: int
    imageBase64: str
    width: int
    height: int


class ProcessScoreResponse(BaseModel):
    scoreId: str
    musicXml: str
    boundingBoxes: list[NoteBoundingBox]
    pages: list[ScorePage]


class ProcessScoreJob(BaseModel):
    jobId: str
    status: str  # queued | running | done | failed
    pagesDone: int
    pagesTotal: int | None
    queuePosition: int
    # What the worker is doing right now (oemer's own stage names).
    stage: str | None = None
    # CPU the worker has used so far -- proof it is actually working, and
    # the basis for `warning`. None where it can't be measured.
    cpuSeconds: float | None = None
    # Set when the worker is alive but barely getting any CPU.
    warning: str | None = None
    error: str | None = None


class NoteDetectionEvent(BaseModel):
    type: str = "NOTE_DETECTION"
    notes: list[str]
    confidence: float
    timestamp: float
