import json
import time

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.models import NoteBoundingBox
from app.services import pitch_detection
from app.services.position_markov import MarkovConfig, MarkovPositionTracker
from app.services.score_timeline import build_timeline

router = APIRouter()

# Tuning for the live page-turner use, as opposed to the offline benchmark
# (which keeps MarkovConfig()'s defaults). The reader tells us where they
# start (top of the score, or a click), so:
#  * a locality window -- the belief update each frame is confined to a
#    band around the current estimate, so a repeated passage elsewhere on
#    the page cannot pull the highlight to it, ever. Forward slack is ~1.5
#    staff lines for catching up a lag; backward is tight.
#  * jumping made much cheaper to not do, and a touch more smoothing, since
#    a highlight that twitches is worse here than one that resolves a frame
#    or two later.
LIVE_CONFIG = MarkovConfig(
    search_ahead=40,
    search_behind=10,
    # Only applies while the tracker is still unsettled, where the job is
    # to *find* the reader, not to hold a position: this is the mass spread
    # uniformly over the score each frame, so it sets how fast belief can
    # migrate to a distant hypothesis. It was 1e-7 -- tuned when a session
    # was assumed to start at the top of a single page, where there is
    # nowhere to migrate to. On a whole-sheet score (1000-2000 onsets) that
    # starves the search. A settled tracker still uses
    # jump_probability_confident, so none of this loosens steady tracking.
    jump_probability=0.03,
    jump_probability_confident=1e-12,
    # Softer than the 0.12 this was, which over-sharpened single frames on
    # real (noisy) recordings and let one bad frame outvote the accumulated
    # evidence. Swept against whole-sheet scores with real ground truth.
    temperature=0.25,
)


class ReportedPosition:
    """Rate-limits the onset index sent to the client.

    The belief still moves freely inside the tracker; this only smooths
    what is shown. Normal playing advances about one onset every couple of
    frames, so a small per-frame step never holds a real performance back,
    but a single frame fooled by a repeat elsewhere in the piece can no
    longer teleport the highlight across the page. A genuinely large move
    (a restart, skipping a repeat) still happens once the tracker reports
    the new spot consistently for ``confirm`` frames. A hint (scroll or
    click) sets the position outright.
    """

    def __init__(self, index: int = 0, step: int = 2, back: int = 3, confirm: int = 12,
                 acquired: bool = False):
        self.index = index
        self._step = step
        self._back = back
        self._confirm = confirm
        self._pending: int | None = None
        self._pending_run = 0
        self._acquired = acquired

    def update(self, target: int, settled: bool = False) -> int:
        # Before the tracker has ever settled there is no established
        # position to protect: the reported index is still the placeholder
        # it was constructed with, and creeping towards the belief two
        # onsets at a time just means showing the reader a spot the model
        # already knows is wrong -- for up to ten seconds, on a score with
        # a few thousand onsets. So during acquisition the report follows
        # the belief outright, and the rate limiter takes over from the
        # first time the tracker is confident.
        if not self._acquired:
            self._acquired = settled
            self.index = target
            return self.index

        delta = target - self.index
        if -self._back <= delta <= self._step:
            self.index = target
            self._pending = None
            self._pending_run = 0
            return self.index

        if self._pending is not None and abs(target - self._pending) <= 2:
            self._pending_run += 1
        else:
            self._pending = target
            self._pending_run = 1

        if self._pending_run >= self._confirm:
            self.index = target
            self._pending = None
            self._pending_run = 0
        else:
            self.index += self._step if delta > 0 else -self._back
        return self.index

    def set(self, index: int) -> None:
        self.index = index
        self._pending = None
        self._pending_run = 0
        # The reader just said where they are, so there is now a position
        # worth protecting even if the tracker isn't confident yet.
        self._acquired = True


class _Session:
    def __init__(self, tracker: MarkovPositionTracker, start_index: int, acquired: bool) -> None:
        self.tracker = tracker
        self.reported = ReportedPosition(index=start_index, acquired=acquired)


@router.websocket("/ws/track-audio")
async def track_audio(websocket: WebSocket) -> None:
    """Streams audio in, streams score position out.

    Protocol:
      * Send a JSON text frame ``{"type": "INIT", "notes": [...bounding
        boxes...], "tempoBpm"?: number, "startOnsetIndex"?: number}`` once,
        before any audio, to enable position tracking. Without a
        ``startOnsetIndex`` the tracker's belief starts uniform across the
        whole score -- the reader may start playing anywhere on the page,
        not just the top -- and locks on from audio evidence alone. Pass
        ``startOnsetIndex`` only when the caller actually knows where
        playback begins (e.g. resuming a previous session); it seeds the
        belief there instead. Without an INIT the socket still runs raw
        pitch detection and replies with ``NOTE_DETECTION`` frames.
      * Send binary PCM16 mono 16 kHz frames (~75 ms each) for the audio.
      * Send ``{"type": "HINT", "onsetIndex": n, "firm"?: bool}`` when the
        reader scrolls (soft) or clicks a spot on the sheet (firm) to fold
        that correction into the belief and move the reported position
        there immediately.

    With a score initialised, every audio frame is replied to with a
    ``POSITION`` frame: the onset the model believes is sounding
    (``onsetIndex``, rate-limited for a steady highlight) plus how sure it
    is, and the ``notes``/``rms`` fields a ``NOTE_DETECTION`` frame carries
    so the detection log still works.
    """
    await websocket.accept()
    start_time = time.monotonic()
    detector = pitch_detection.PitchDetector()
    session: _Session | None = None

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            text = message.get("text")
            if text is not None:
                session = _handle_control(text, session)
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

            if session is not None:
                usable = len(chunk) - (len(chunk) % 2)
                frame = np.frombuffer(chunk[:usable], dtype="<i2").astype(np.float64)
                estimate = session.tracker.observe(frame)
                response["type"] = "POSITION"
                settled = estimate.confidence >= LIVE_CONFIG.jump_confidence_gate
                response["onsetIndex"] = session.reported.update(estimate.index, settled)
                response["positionConfidence"] = round(estimate.confidence, 3)

            await websocket.send_json(response)
    except WebSocketDisconnect:
        pass


def _handle_control(text: str, session: _Session | None) -> _Session | None:
    """Applies an INIT / HINT control frame, returning the (maybe new) session."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return session

    kind = payload.get("type")
    if kind == "INIT":
        try:
            notes = [NoteBoundingBox(**note) for note in payload.get("notes", [])]
        except (ValidationError, TypeError):
            return session
        timeline = build_timeline(notes, tempo_bpm=payload.get("tempoBpm", 99.0))
        if not timeline:
            return None
        tracker = MarkovPositionTracker(timeline, LIVE_CONFIG)
        start_index = 0
        raw_start = payload.get("startOnsetIndex")
        if raw_start is not None:
            # The caller actually knows where playback begins (e.g.
            # resuming a session) -- seed the belief there. Otherwise leave
            # the tracker's own uniform prior alone so a performer starting
            # anywhere on the page is found from audio evidence, not assumed
            # to be at the top.
            start_index = max(0, min(int(raw_start), len(timeline) - 1))
            tracker.apply_hint(start_index, strength=0.9, width=3.0)
        return _Session(tracker, start_index, acquired=raw_start is not None)

    if kind == "HINT" and session is not None:
        try:
            index = int(payload["onsetIndex"])
        except (KeyError, TypeError, ValueError):
            return session
        firm = bool(payload.get("firm", False))
        estimate = session.tracker.apply_hint(
            index,
            strength=0.99 if firm else 0.95,
            width=1.2 if firm else 3.0,
        )
        session.reported.set(estimate.index)

    return session
