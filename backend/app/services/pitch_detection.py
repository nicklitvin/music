"""Polyphonic pitch detection over streamed PCM audio.

Real frequency-domain detection: each incoming 16kHz mono PCM16 chunk is
appended to a short rolling buffer (more history than a single ~75ms chunk
gives better frequency resolution), then the buffer's spectrum is peak-
picked to find one or more concurrent fundamental frequencies -- so both a
single note and a chord (multiple simultaneous notes) are reported as
multiple entries in `notes` when present. Peaks that land near an integer
multiple of an already-accepted fundamental are treated as that note's
overtone rather than a separate note, which is what keeps a single
harmonically-rich note (e.g. a real piano/violin tone, not a pure sine)
from being reported as a chord of itself.

This is still a fairly naive detector (susceptible to octave errors,
very dense/dissonant chords, and background noise) -- swap out
`_detect_pitches` for a real polyphonic model (e.g. Onsets and Frames) if
more accuracy is needed. The `PitchDetector` object, the streaming contract
(binary in, JSON NOTE_DETECTION out), and the router do not need to change
to do so.
"""

import math

import numpy as np

SAMPLE_RATE = 16000
SILENCE_RMS_THRESHOLD = 500.0

# Rolling FFT window: bigger = better frequency resolution, more lag.
# 4096 samples @ 16kHz = 256ms, resolves down to ~3.9Hz/bin before
# zero-padding interpolation.
WINDOW_SAMPLES = 4096

# Restrict candidate fundamentals to a plausible musical range (piano A0-C8)
# so non-musical energy (noise, breath, room hum) doesn't get reported as a
# note.
MIN_FREQUENCY_HZ = 27.5
MAX_FREQUENCY_HZ = 4200.0

# A candidate peak must reach this fraction of the strongest in-range peak's
# magnitude to be considered at all -- keeps spectral noise out of chords.
PEAK_RELATIVE_THRESHOLD = 0.15

# How many times an already-accepted note's fundamental is multiplied out
# (2x, 3x, ...) when checking whether a later, quieter peak is just that
# note's overtone rather than a separate concurrent note.
MAX_HARMONIC_MULTIPLE = 8

# Relative frequency tolerance (as a fraction of the harmonic's expected
# frequency) for treating a peak as an overtone of an already-accepted note.
HARMONIC_TOLERANCE = 0.03

MAX_CONCURRENT_NOTES = 6

_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _freq_to_note_name(freq_hz: float) -> str:
    midi = round(69 + 12 * math.log2(freq_hz / 440.0))
    name = _NOTE_NAMES[midi % 12]
    octave = midi // 12 - 1
    return f"{name}{octave}"


def _is_harmonic_of(freq_hz: float, fundamental_hz: float) -> bool:
    for multiple in range(2, MAX_HARMONIC_MULTIPLE + 1):
        expected = fundamental_hz * multiple
        if abs(freq_hz - expected) <= expected * HARMONIC_TOLERANCE:
            return True
    return False


def _detect_pitches(buffer: np.ndarray, sample_rate: int) -> list[str]:
    if len(buffer) < 64:
        return []

    windowed = buffer * np.hanning(len(buffer))
    # Zero-pad before the FFT: doesn't add real resolution, but interpolates
    # the spectrum finely enough to locate each peak's true bin more
    # precisely than the raw window length would.
    fft_len = len(windowed) * 4
    spectrum = np.abs(np.fft.rfft(windowed, n=fft_len))
    freqs = np.fft.rfftfreq(fft_len, d=1 / sample_rate)

    in_range = (freqs >= MIN_FREQUENCY_HZ) & (freqs <= MAX_FREQUENCY_HZ)
    spectrum = np.where(in_range, spectrum, 0.0)

    peak_max = spectrum.max()
    if peak_max <= 0:
        return []

    # Local maxima: bins strictly louder than both neighbors.
    is_local_max = np.zeros_like(spectrum, dtype=bool)
    is_local_max[1:-1] = (spectrum[1:-1] > spectrum[:-2]) & (spectrum[1:-1] > spectrum[2:])
    candidate_indices = np.nonzero(is_local_max & (spectrum >= peak_max * PEAK_RELATIVE_THRESHOLD))[0]

    # Strongest peaks first: each one is either a new note's fundamental, or
    # (if it lines up with a multiple of a note already accepted) that
    # note's overtone.
    ranked = sorted(candidate_indices, key=lambda i: spectrum[i], reverse=True)

    fundamentals_hz: list[float] = []
    notes: list[str] = []
    for index in ranked:
        freq = freqs[index]
        if any(_is_harmonic_of(freq, f0) for f0 in fundamentals_hz):
            continue

        name = _freq_to_note_name(freq)
        if name not in notes:
            fundamentals_hz.append(freq)
            notes.append(name)
        if len(notes) >= MAX_CONCURRENT_NOTES:
            break

    return notes


class DetectionResult:
    __slots__ = ("notes", "confidence", "rms")

    def __init__(self, notes: list[str], confidence: float, rms: float):
        self.notes = notes
        self.confidence = confidence
        self.rms = rms


class PitchDetector:
    """Stateful detector for one streaming session (one WebSocket connection).

    Holds a short rolling buffer of recent PCM so each detection benefits
    from more than just the latest ~75ms chunk's worth of samples. Not
    shared across connections/sessions.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, window_samples: int = WINDOW_SAMPLES):
        self._sample_rate = sample_rate
        self._window_samples = window_samples
        self._buffer = np.zeros(0, dtype=np.float64)

    def process(self, pcm16_bytes: bytes) -> DetectionResult:
        usable_len = len(pcm16_bytes) - (len(pcm16_bytes) % 2)
        chunk = np.frombuffer(pcm16_bytes[:usable_len], dtype="<i2").astype(np.float64)

        rms = float(np.sqrt(np.mean(np.square(chunk)))) if len(chunk) else 0.0

        self._buffer = np.concatenate([self._buffer, chunk])[-self._window_samples :]

        if rms < SILENCE_RMS_THRESHOLD:
            return DetectionResult(notes=[], confidence=0.0, rms=round(rms, 1))

        notes = _detect_pitches(self._buffer, self._sample_rate)
        confidence = min(rms / 10000, 1.0)
        return DetectionResult(notes=notes, confidence=round(confidence, 2), rms=round(rms, 1))
