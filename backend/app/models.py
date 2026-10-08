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
    error: str | None = None


class NoteDetectionEvent(BaseModel):
    type: str = "NOTE_DETECTION"
    notes: list[str]
    confidence: float
    timestamp: float
