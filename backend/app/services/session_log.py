"""A per-session log of live tracking: what was heard, and what was said
back, so a session that tracked badly can be picked apart afterwards.

One file per websocket session, as JSON lines, under
`settings.session_log_dir` (default ~/music-logs/sessions, outside the
deploy checkout so a deploy never touches it), in a folder per day. The
file is gzipped when the session ends; a session cut off by a restart
stays as plain .jsonl. Nothing is ever deleted -- these are kept
indefinitely for now, at roughly 1 MB an hour of playing once compressed.

Every line has `t` (seconds since the socket opened) and `event`:

  open      the socket connected
  init      the score was sent: how many notes and onsets, tempo, start
  frame     one ~75 ms chunk of audio --
              rms         its loudness (PCM16 units)
              fft         notes the FFT pitch detector found
              heard       pitches the learned model hears ringing, and
              struck      ones it hears being struck (both may cover
                          several model hops, or none while it buffers)
              belief      the onset the tracker believes is sounding
              onset       the onset reported to the client (rate-limited)
              confidence  how sure the tracker is
              expected    the pitches the score has at `onset`
  hint      the reader picked a spot (`onset`, `firm`)
  viewport  the window the model is confined to (`first`, `last` onset)
  close     the socket closed, with how many frames it carried

The audio itself is never written -- the app promises it is analysed as it
arrives and discarded -- only what was detected in it.
"""

import gzip
import json
import logging
import os
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np

from app.config import settings
from app.services.note_estimation import MIN_MIDI, NUM_PITCHES, midi_to_pitch

log = logging.getLogger("uvicorn.error")

# A pitch counts as heard at this share of the strongest one in its frame.
HEARD_SHARE = 0.5


def pitches_in(salience: list[np.ndarray]) -> tuple[list[str], list[str]]:
    """(ringing, struck) pitch names across the learned model's hops.

    Each hop's vector is the sustained-pitch half then the attack half (see
    streaming_transcription.combined_salience); all zeros is silence.
    """
    ringing: list[str] = []
    struck: list[str] = []
    for vector in salience:
        for half, out in ((vector[:NUM_PITCHES], ringing), (vector[NUM_PITCHES:], struck)):
            peak = float(half.max(initial=0.0))
            if peak <= 0:
                continue
            for index in np.flatnonzero(half >= peak * HEARD_SHARE):
                name = midi_to_pitch(MIN_MIDI + int(index))
                if name not in out:
                    out.append(name)
    return ringing, struck


class SessionLog:
    """Appends one session's events. Never raises: a full disk or a bad
    path costs the log, not the session."""

    def __init__(self, directory: str | None = None) -> None:
        self._started = time.monotonic()
        self.frames = 0
        self.path: Path | None = None
        self._file = None
        directory = settings.session_log_dir if directory is None else directory
        if not directory:
            return
        now = datetime.now()
        try:
            folder = Path(os.path.expanduser(directory)) / now.strftime("%Y-%m-%d")
            folder.mkdir(parents=True, exist_ok=True)
            self.path = folder / f"{now.strftime('%H%M%S')}-{uuid.uuid4().hex[:8]}.jsonl"
            self._file = open(self.path, "a", encoding="utf-8")
        except OSError as exc:
            log.warning("session log disabled: %s", exc)
            self._file = None

    def event(self, kind: str, **fields) -> None:
        if self._file is None:
            return
        if kind == "frame":
            self.frames += 1
        record = {"t": round(time.monotonic() - self._started, 3), "event": kind, **fields}
        try:
            self._file.write(json.dumps(record, separators=(",", ":")) + "\n")
        except (OSError, ValueError) as exc:
            log.warning("session log %s stopped: %s", self.path, exc)
            self._file = None

    def close(self) -> Path | None:
        """Close and compress; returns where the log ended up."""
        if self._file is None:
            return None
        self.event("close", frames=self.frames)
        try:
            self._file.close()
            self._file = None
            packed = self.path.with_suffix(".jsonl.gz")
            with open(self.path, "rb") as source, gzip.open(packed, "wb") as target:
                shutil.copyfileobj(source, target)
            self.path.unlink()
            self.path = packed
        except OSError as exc:
            log.warning("session log %s not compressed: %s", self.path, exc)
        return self.path
