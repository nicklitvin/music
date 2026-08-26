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

            # Sent for every chunk, including silence (empty `notes`), so the
            # client can see that audio is actually arriving and inspect the
            # raw signal level (`rms`) rather than only the final decision.
            await websocket.send_json(
                {
                    "type": "NOTE_DETECTION",
                    "notes": result.notes,
                    "confidence": result.confidence,
                    "rms": result.rms,
                    "timestamp": round(time.monotonic() - start_time, 3),
                }
            )
    except WebSocketDisconnect:
        pass
