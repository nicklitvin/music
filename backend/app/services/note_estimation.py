"""Turning audio into per-pitch evidence.

Deliberately separate from deciding *where in a score* that evidence puts
the player (position_tracking.py). This module answers only "what notes are
sounding right now", and does it without any knowledge of the score.

The output is a salience vector over MIDI pitches rather than a list of
discrete note names. Committing to a hard set of notes throws away exactly
the information the position model needs: a note the estimator is unsure
about still carries evidence, and forcing a yes/no decision on it means a
single mistake propagates with no way to recover. Downstream consumers that
do want discrete notes can threshold the vector themselves.
"""

from __future__ import annotations

import re

import numpy as np

SAMPLE_RATE = 16000
SILENCE_RMS_THRESHOLD = 500.0

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_PITCH_RE = re.compile(r"^([A-G]#?)(-?\d+)$")

MIN_MIDI = 21  # A0
MAX_MIDI = 108  # C8
NUM_PITCHES = MAX_MIDI - MIN_MIDI + 1

# Frames arrive every ~75ms, but 75ms of audio only resolves ~13Hz, while
# adjacent semitones in the bass are a couple of Hz apart -- a single frame
# genuinely cannot tell low notes apart. Analysis therefore runs over a
# rolling window of recent samples, longer than one frame, while still
# producing an estimate every frame. Measured against the synthesized
# reference, 2048 samples (128ms) is the peak: shorter loses the bass,
# longer smears across onsets and blurs when the music actually moved on.
ANALYSIS_WINDOW_SAMPLES = 2048


def pitch_to_midi(pitch: str) -> int | None:
    match = _PITCH_RE.match(pitch)
    if not match:
        return None
    return (int(match.group(2)) + 1) * 12 + NOTE_NAMES.index(match.group(1))


def midi_to_pitch(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def midi_to_hz(midi: int) -> float:
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def is_silent(frame: np.ndarray) -> bool:
    return float(np.sqrt(np.mean(np.square(frame)))) < SILENCE_RMS_THRESHOLD


class HarmonicSalienceEstimator:
    """Scores every piano pitch by the energy at its fundamental and
    harmonics.

    Summing a pitch's harmonics (rather than reading the fundamental bin
    alone) is what makes this usable on real instrument tones, where the
    fundamental is often not the strongest partial. The harmonic weights
    fall off so that a low note's overtones cannot fully impersonate a
    fundamental an octave up.

    Stateful: keeps a rolling audio buffer across calls, so feed it
    consecutive frames from one performance and use a fresh instance per
    performance.
    """

    NUM_HARMONICS = 5
    HARMONIC_WEIGHTS = (1.0, 0.5, 0.33, 0.25, 0.2)
    # Half-width of the band searched around each harmonic, as a fraction of
    # its frequency. Wide enough to absorb tuning error and FFT bin spacing,
    # narrow enough not to bleed into the neighbouring semitone (~5.9%).
    BAND_TOLERANCE = 0.02

    def __init__(self, window_samples: int = ANALYSIS_WINDOW_SAMPLES, sample_rate: int = SAMPLE_RATE):
        self.window_samples = window_samples
        self.sample_rate = sample_rate
        self._history = np.zeros(0)
        self._bins: list[list[np.ndarray]] | None = None

    def _window(self, frame: np.ndarray) -> np.ndarray:
        """The most recent `window_samples`, zero-padded while still short.

        A constant length keeps the FFT size stable, which the precomputed
        harmonic bin indices depend on -- they are only valid for one FFT
        size.
        """
        self._history = np.concatenate([self._history, frame])[-self.window_samples :]
        if len(self._history) < self.window_samples:
            return np.concatenate([np.zeros(self.window_samples - len(self._history)), self._history])
        return self._history

    def _harmonic_bins(self, freqs: np.ndarray) -> list[list[np.ndarray]]:
        bins = []
        for midi in range(MIN_MIDI, MAX_MIDI + 1):
            hz = midi_to_hz(midi)
            per_harmonic = []
            for harmonic in range(1, self.NUM_HARMONICS + 1):
                centre = hz * harmonic
                low, high = centre * (1 - self.BAND_TOLERANCE), centre * (1 + self.BAND_TOLERANCE)
                per_harmonic.append(np.nonzero((freqs >= low) & (freqs <= high))[0])
            bins.append(per_harmonic)
        return bins

    def estimate(self, frame: np.ndarray) -> np.ndarray:
        """Unit-norm salience over MIDI pitches MIN_MIDI..MAX_MIDI.

        Returns all zeros for silence, which callers should treat as "no
        evidence" rather than "no notes".
        """
        window = self._window(frame)
        if is_silent(frame):
            return np.zeros(NUM_PITCHES)

        windowed = window * np.hanning(len(window))
        fft_len = len(windowed) * 2
        magnitude = np.abs(np.fft.rfft(windowed, n=fft_len))
        if self._bins is None:
            self._bins = self._harmonic_bins(np.fft.rfftfreq(fft_len, d=1 / self.sample_rate))

        salience = np.zeros(NUM_PITCHES)
        for index, per_harmonic in enumerate(self._bins):
            total = 0.0
            for weight, indices in zip(self.HARMONIC_WEIGHTS, per_harmonic):
                if indices.size:
                    total += weight * float(magnitude[indices].max())
            salience[index] = total

        norm = np.linalg.norm(salience)
        return salience / norm if norm > 0 else salience

    def top_pitches(self, salience: np.ndarray, count: int = 6, relative_threshold: float = 0.3) -> list[str]:
        """Discrete note names, for logging and display only.

        The position model should consume the salience vector directly --
        thresholding here is lossy, and this is exactly the step that made
        the old melody tracker brittle.
        """
        if not salience.any():
            return []
        peak = salience.max()
        ranked = np.argsort(-salience)[:count]
        return [midi_to_pitch(MIN_MIDI + int(i)) for i in ranked if salience[i] >= peak * relative_threshold]
