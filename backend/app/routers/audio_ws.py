import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services import pitch_detection

router = APIRouter()


@router.websocket("/ws/track-audio")
async def track_audio(websocket: WebSocket) -> None:
    await websocket.accept()
    start_time = time.monotonic()

    try:
        while True:
            chunk = await websocket.receive_bytes()
            result = pitch_detection.detect(chunk)

            if not result.notes:
                continue

            await websocket.send_json(
                {
                    "type": "NOTE_DETECTION",
                    "notes": result.notes,
                    "confidence": result.confidence,
                    "timestamp": round(time.monotonic() - start_time, 3),
                }
            )
    except WebSocketDisconnect:
        pass
