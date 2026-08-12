"""Polyphonic pitch detection over streamed PCM audio.

This is a lightweight energy-gate placeholder, not a real transcription
model. It computes RMS amplitude on each incoming 16kHz mono PCM16 chunk and
reports a fixed note when the signal is loud enough. It exists to prove out
the WebSocket streaming contract (binary in, JSON NOTE_DETECTION out) end to
end. Swap this out for a real polyphonic model (e.g. Onsets and Frames) by
replacing `detect` -- the router and message shape do not need to change.
"""

import array
import math
from dataclasses import dataclass

SAMPLE_RATE = 16000
SILENCE_RMS_THRESHOLD = 500.0


@dataclass
class DetectionResult:
    notes: list[str]
    confidence: float


def _rms(pcm16_bytes: bytes) -> float:
    if not pcm16_bytes:
        return 0.0
    samples = array.array("h")
    samples.frombytes(pcm16_bytes[: len(pcm16_bytes) - (len(pcm16_bytes) % 2)])
    if not samples:
        return 0.0
    sum_squares = sum(sample * sample for sample in samples)
    return math.sqrt(sum_squares / len(samples))


def detect(pcm16_bytes: bytes) -> DetectionResult:
    rms = _rms(pcm16_bytes)
    if rms < SILENCE_RMS_THRESHOLD:
        return DetectionResult(notes=[], confidence=0.0)

    confidence = min(rms / 10000, 1.0)
    return DetectionResult(notes=["C4"], confidence=round(confidence, 2))
