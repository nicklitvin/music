from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import audio_ws, dev, score

app = FastAPI(title="Sheet Music Tracker API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(score.router)
app.include_router(audio_ws.router)
app.include_router(dev.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
