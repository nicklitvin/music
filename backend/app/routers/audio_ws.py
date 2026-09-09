import json
import time

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.models import NoteBoundingBox
from app.services import pitch_detection
from app.services.position_markov import MarkovPositionTracker
from app.services.score_timeline import build_timeline

router = APIRouter()


@router.websocket("/ws/track-audio")
async def track_audio(websocket: WebSocket) -> None:
    """Streams audio in, streams score position out.

    Protocol:
      * Send a JSON text frame ``{"type": "INIT", "notes": [...bounding
        boxes...], "tempoBpm"?: number}`` once, before any audio, to enable
        position tracking. Without it the socket still runs raw pitch
        detection and replies with ``NOTE_DETECTION`` frames (used by the
        pitch-detection tests and any client that only wants notes).
      * Send binary PCM16 mono 16 kHz frames (~75 ms each) for the audio.
      * Send ``{"type": "HINT", "onsetIndex": n}`` when the reader scrolls
        by hand, to fold that correction into the belief.

    With a score initialised, every audio frame is replied to with a
    ``POSITION`` frame: the single onset the model believes is sounding
    (``onsetIndex``) plus how sure it is, and the same ``notes``/``rms``
    fields a ``NOTE_DETECTION`` frame carries so the detection log still
    works. All the score-following logic lives here now -- the client just
    highlights the line the reported onset falls on.
    """
    await websocket.accept()
    start_time = time.monotonic()
    detector = pitch_detection.PitchDetector()
    tracker: MarkovPositionTracker | None = None

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            text = message.get("text")
            if text is not None:
                tracker = _handle_control(text, tracker)
                continue

            chunk = message.get("bytes")
            if not chunk:
                continue

            result = detector.process(chunk)
            response = {
                "type": "NOTE_DETECTION",
                "notes": result.notes,
                "confidence": result.confidence,
                "rms": result.rms,
                "timestamp": round(time.monotonic() - start_time, 3),
            }

            if tracker is not None:
                usable = len(chunk) - (len(chunk) % 2)
                frame = np.frombuffer(chunk[:usable], dtype="<i2").astype(np.float64)
                estimate = tracker.observe(frame)
                response["type"] = "POSITION"
                response["onsetIndex"] = estimate.index
                response["positionConfidence"] = round(estimate.confidence, 3)

            await websocket.send_json(response)
    except WebSocketDisconnect:
        pass


def _handle_control(text: str, tracker: MarkovPositionTracker | None) -> MarkovPositionTracker | None:
    """Applies an INIT / HINT control frame, returning the (maybe new) tracker."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return tracker

    kind = payload.get("type")
    if kind == "INIT":
        try:
            notes = [NoteBoundingBox(**note) for note in payload.get("notes", [])]
        except (ValidationError, TypeError):
            return tracker
        timeline = build_timeline(notes, tempo_bpm=payload.get("tempoBpm", 99.0))
        return MarkovPositionTracker(timeline) if timeline else None

    if kind == "HINT" and tracker is not None:
        try:
            tracker.apply_hint(int(payload["onsetIndex"]))
        except (KeyError, TypeError, ValueError):
            pass

    return tracker
