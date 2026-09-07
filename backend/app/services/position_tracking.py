"""Ways of deciding where in a score a performance currently is.

Several interchangeable strategies live here so they can be compared on the
same input rather than argued about -- see scripts/evaluate_tracking.py,
which scores each one against synthesized audio whose true position at
every instant is known. `DEFAULT_METHOD` names the most accurate one; the
others are kept because they fail in different ways and are worth
re-measuring whenever the audio front-end changes.

Every tracker consumes fixed-size mono PCM frames and reports the index of
the score onset (see score_timeline.py) it believes is sounding. They
differ in what they extract from the audio and how they choose among
candidate positions:

- `MelodyPitchTracker` mirrors the live frontend pipeline: discrete pitch
  detection, take the highest note, jump to the nearest matching onset.
  Cheap, and the most sensitive to a single spurious high detection.
- `ChromaTemplateTracker` compares pitch-class energy, ignoring octave.
  Robust to octave errors, but blind to them too, so repeated material an
  octave apart is indistinguishable.
- `SalienceTemplateTracker` compares per-pitch harmonic salience against
  each candidate onset's expected pitches, with a locality prior. Keeps
  octave information and weighs every sounding note rather than just the
  top one.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np

from app.services.score_timeline import TimelineOnset

SAMPLE_RATE = 16000
SILENCE_RMS_THRESHOLD = 500.0

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_PITCH_RE = re.compile(r"^([A-G]#?)(-?\d+)$")

MIN_MIDI = 21  # A0
MAX_MIDI = 108  # C8


def pitch_to_midi(pitch: str) -> int | None:
    match = _PITCH_RE.match(pitch)
    if not match:
        return None
    return (int(match.group(2)) + 1) * 12 + NOTE_NAMES.index(match.group(1))


def midi_to_hz(midi: int) -> float:
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


# How quickly a struck piano note's energy decays, in seconds -- a note
# struck this long ago contributes ~1/e of its original weight to what is
# heard now. Matches the synthesis envelope and is roughly right for a real
# piano.
NOTE_DECAY_SECONDS = 1.0


def sounding_weights(timeline: list[TimelineOnset], index: int) -> dict[int, float]:
    """What is actually audible at onset `index`, as {midi: weight}.

    Not the same as the onset's own notes: a note struck earlier keeps
    ringing (a held bass note under several melody notes), so the audio at
    this moment is a decaying mixture of everything still sounding. A
    template built only from the notes that *start* here fails as soon as
    the score sustains anything -- the observed spectrum contains energy
    the template does not predict, and some later onset matches it better.
    """
    now = timeline[index].start_seconds
    weights: dict[int, float] = {}

    for onset in timeline[: index + 1]:
        age = now - onset.start_seconds
        for note, duration in zip(onset.notes, onset.note_durations):
            if onset.start_seconds + duration <= now:
                continue  # already released
            midi = pitch_to_midi(note.pitch)
            if midi is None or not (MIN_MIDI <= midi <= MAX_MIDI):
                continue
            weight = math.exp(-age / NOTE_DECAY_SECONDS)
            weights[midi] = max(weights.get(midi, 0.0), weight)

    return weights


@dataclass
class TrackerConfig:
    # How far ahead/behind the current position candidates are considered,
    # in onsets. Wide enough to recover from a missed passage, narrow
    # enough that a repeat elsewhere in the piece can't win on a whim.
    search_ahead: int = 24
    search_behind: int = 6
    # Locality prior: a candidate's score is multiplied by
    # exp(-distance / decay). Forward decays slower than backward, and
    # backward takes an extra flat penalty, because music is read forwards.
    forward_decay: float = 12.0
    backward_decay: float = 4.0
    backward_penalty: float = 0.35
    # A frame must beat the current position's own score by this factor
    # before the tracker moves, which stops it dithering between two
    # near-equally-good candidates.
    switch_margin: float = 1.02


class BaseTracker:
    """Common bookkeeping: current position and the locality prior."""

    name = "base"

    def __init__(self, timeline: list[TimelineOnset], config: TrackerConfig | None = None):
        self.timeline = timeline
        self.config = config or TrackerConfig()
        self.position = 0
        self._history = np.zeros(0)

    def _analysis_window(self, frame: np.ndarray) -> np.ndarray:
        """The most recent ANALYSIS_WINDOW_SAMPLES, including this frame.

        Always exactly that many samples, zero-padded at the start while
        the stream is still short. A constant length keeps the FFT size --
        and so the precomputed harmonic bin indices, which are only valid
        for one FFT size -- stable from the very first frame.
        """
        self._history = np.concatenate([self._history, frame])[-ANALYSIS_WINDOW_SAMPLES:]
        if len(self._history) < ANALYSIS_WINDOW_SAMPLES:
            return np.concatenate([np.zeros(ANALYSIS_WINDOW_SAMPLES - len(self._history)), self._history])
        return self._history

    def _candidate_range(self) -> range:
        start = max(0, self.position - self.config.search_behind)
        stop = min(len(self.timeline), self.position + self.config.search_ahead + 1)
        return range(start, stop)

    def _locality(self, index: int) -> float:
        distance = index - self.position
        if distance >= 0:
            return math.exp(-distance / self.config.forward_decay)
        return self.config.backward_penalty * math.exp(distance / self.config.backward_decay)

    def observe(self, frame: np.ndarray) -> int:
        raise NotImplementedError

    def _commit(self, scores: dict[int, float]) -> int:
        if not scores:
            return self.position
        best_index = max(scores, key=lambda i: scores[i])
        if best_index != self.position:
            current = scores.get(self.position, 0.0)
            if scores[best_index] < current * self.config.switch_margin:
                return self.position
            self.position = best_index
        return self.position


# Frames arrive every ~75ms, but 75ms of audio only resolves ~13Hz, and
# adjacent semitones down in the bass are a couple of Hz apart -- so a
# single frame cannot tell low notes apart at all. Analysis therefore runs
# over a rolling window of the most recent samples, longer than one frame,
# while still reporting a position every frame. Measured on the synthesized
# reference, 2048 samples (128ms) is the peak: shorter loses the bass,
# longer smears across onsets and blurs exactly when the music moved on.
ANALYSIS_WINDOW_SAMPLES = 2048


def _spectrum(frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    windowed = frame * np.hanning(len(frame))
    fft_len = len(windowed) * 2
    magnitude = np.abs(np.fft.rfft(windowed, n=fft_len))
    freqs = np.fft.rfftfreq(fft_len, d=1 / SAMPLE_RATE)
    return magnitude, freqs


def _is_silent(frame: np.ndarray) -> bool:
    return float(np.sqrt(np.mean(np.square(frame)))) < SILENCE_RMS_THRESHOLD


# --------------------------------------------------------------------------
# Method 1: discrete pitch detection + highest note (the live pipeline)
# --------------------------------------------------------------------------


class MelodyPitchTracker(BaseTracker):
    """Detects discrete pitches, keeps the highest, jumps to the nearest
    onset containing it. Mirrors the frontend's MelodyTracker, and inherits
    its weakness: one spurious high peak overrides the real melody note.
    """

    name = "melody-pitch"

    PEAK_RELATIVE_THRESHOLD = 0.15
    MAX_CONCURRENT = 6

    def __init__(self, timeline: list[TimelineOnset], config: TrackerConfig | None = None):
        super().__init__(timeline, config)
        self._highest_midi = [
            max((pitch_to_midi(p) or -1) for p in onset.pitches) for onset in timeline
        ]

    def _detect_pitches(self, frame: np.ndarray) -> list[int]:
        magnitude, freqs = _spectrum(frame)
        in_range = (freqs >= midi_to_hz(MIN_MIDI)) & (freqs <= midi_to_hz(MAX_MIDI))
        magnitude = np.where(in_range, magnitude, 0.0)
        peak = magnitude.max()
        if peak <= 0:
            return []

        is_local_max = np.zeros_like(magnitude, dtype=bool)
        is_local_max[1:-1] = (magnitude[1:-1] > magnitude[:-2]) & (magnitude[1:-1] > magnitude[2:])
        candidates = np.nonzero(is_local_max & (magnitude >= peak * self.PEAK_RELATIVE_THRESHOLD))[0]
        ranked = sorted(candidates, key=lambda i: magnitude[i], reverse=True)[: self.MAX_CONCURRENT]
        return [round(69 + 12 * math.log2(freqs[i] / 440.0)) for i in ranked if freqs[i] > 0]

    def observe(self, frame: np.ndarray) -> int:
        if _is_silent(frame):
            return self.position
        detected = self._detect_pitches(frame)
        if not detected:
            return self.position

        target = max(detected)
        scores = {
            index: self._locality(index)
            for index in self._candidate_range()
            if self._highest_midi[index] == target
        }
        return self._commit(scores)


# --------------------------------------------------------------------------
# Method 2: pitch-class (chroma) template matching
# --------------------------------------------------------------------------


def _chroma_template(timeline: list[TimelineOnset], index: int) -> np.ndarray:
    vector = np.zeros(12)
    for midi, weight in sounding_weights(timeline, index).items():
        vector[midi % 12] += weight
    norm = np.linalg.norm(vector)
    return vector / norm if norm > 0 else vector


class ChromaTemplateTracker(BaseTracker):
    """Folds spectral energy into 12 pitch classes and cosine-matches it
    against each candidate onset's pitch classes. Octave-agnostic, so it
    shrugs off octave errors but also can't tell octaves apart.
    """

    name = "chroma-template"

    def __init__(self, timeline: list[TimelineOnset], config: TrackerConfig | None = None):
        super().__init__(timeline, config)
        self._templates = np.array([_chroma_template(timeline, i) for i in range(len(timeline))])

    def _chroma(self, frame: np.ndarray) -> np.ndarray:
        magnitude, freqs = _spectrum(frame)
        vector = np.zeros(12)
        for midi in range(MIN_MIDI, MAX_MIDI + 1):
            hz = midi_to_hz(midi)
            low, high = hz * 0.97, hz * 1.03
            band = magnitude[(freqs >= low) & (freqs <= high)]
            if band.size:
                vector[midi % 12] += float(band.max())
        norm = np.linalg.norm(vector)
        return vector / norm if norm > 0 else vector

    def observe(self, frame: np.ndarray) -> int:
        if _is_silent(frame):
            return self.position
        observed = self._chroma(frame)
        if not observed.any():
            return self.position

        scores = {
            index: float(self._templates[index] @ observed) * self._locality(index)
            for index in self._candidate_range()
        }
        return self._commit(scores)


# --------------------------------------------------------------------------
# Method 3: per-pitch harmonic salience template matching
# --------------------------------------------------------------------------


def _salience_template(timeline: list[TimelineOnset], index: int) -> np.ndarray:
    vector = np.zeros(MAX_MIDI - MIN_MIDI + 1)
    for midi, weight in sounding_weights(timeline, index).items():
        vector[midi - MIN_MIDI] = weight
    norm = np.linalg.norm(vector)
    return vector / norm if norm > 0 else vector


class SalienceTemplateTracker(BaseTracker):
    """Scores every pitch by summing energy at its fundamental and
    harmonics, then cosine-matches that salience curve against each
    candidate onset's expected pitches, weighted by locality.

    Uses all sounding notes rather than only the highest, so one bad peak
    dilutes the match instead of dictating it, and keeps octave information
    that chroma discards.
    """

    name = "salience-template"

    NUM_HARMONICS = 5
    # Harmonic weights fall off, matching how overtone energy decays -- and
    # limiting how much a harmonic of a lower note can masquerade as a
    # fundamental an octave up.
    HARMONIC_WEIGHTS = (1.0, 0.5, 0.33, 0.25, 0.2)

    def __init__(self, timeline: list[TimelineOnset], config: TrackerConfig | None = None):
        super().__init__(timeline, config)
        self._templates = np.array([_salience_template(timeline, i) for i in range(len(timeline))])
        self._bin_cache: list[list[np.ndarray]] | None = None

    def _harmonic_bins(self, freqs: np.ndarray) -> list[list[np.ndarray]]:
        # Bin indices per (pitch, harmonic) depend only on the FFT size, so
        # compute them once and reuse across frames.
        bins: list[list[np.ndarray]] = []
        for midi in range(MIN_MIDI, MAX_MIDI + 1):
            hz = midi_to_hz(midi)
            per_harmonic = []
            for harmonic in range(1, self.NUM_HARMONICS + 1):
                target = hz * harmonic
                low, high = target * 0.98, target * 1.02
                per_harmonic.append(np.nonzero((freqs >= low) & (freqs <= high))[0])
            bins.append(per_harmonic)
        return bins

    def _salience(self, frame: np.ndarray) -> np.ndarray:
        magnitude, freqs = _spectrum(frame)
        if self._bin_cache is None:
            self._bin_cache = self._harmonic_bins(freqs)  # type: ignore[assignment]

        vector = np.zeros(MAX_MIDI - MIN_MIDI + 1)
        for offset, per_harmonic in enumerate(self._bin_cache):  # type: ignore[arg-type]
            total = 0.0
            for weight, indices in zip(self.HARMONIC_WEIGHTS, per_harmonic):
                if indices.size:
                    total += weight * float(magnitude[indices].max())
            vector[offset] = total

        norm = np.linalg.norm(vector)
        return vector / norm if norm > 0 else vector

    def observe(self, frame: np.ndarray) -> int:
        if _is_silent(frame):
            return self.position
        observed = self._salience(frame)
        if not observed.any():
            return self.position

        scores = {
            index: float(self._templates[index] @ observed) * self._locality(index)
            for index in self._candidate_range()
        }
        return self._commit(scores)


# --------------------------------------------------------------------------
# Method 4: HMM / Viterbi alignment over harmonic salience
# --------------------------------------------------------------------------


class HmmSalienceTracker(SalienceTemplateTracker):
    """Aligns audio to the score with a Viterbi decode instead of choosing
    per frame.

    A single frame is genuinely ambiguous -- neighbouring onsets share most
    of their sounding notes, so the correct onset is only the best match
    about a third of the time (though nearly always in the top few). The
    greedy trackers above have to commit on that alone, and one bad jump is
    unrecoverable once the search window has moved past the truth.

    Here every onset is a hidden state scored over the whole history:
    observation likelihood from template similarity, plus transitions that
    only ever move forwards, prefer staying put for about as long as the
    onset actually lasts, and allow small skips for onsets the detector
    misses. The reported position is the current best path, so evidence
    accumulates and momentary ambiguity is resolved by what came before
    rather than by a locality guess.
    """

    name = "hmm-salience"

    # All four constants below were chosen by sweeping them against the
    # synthesized reference (scripts/evaluate_tracking.py), not by feel.

    # Sharpens cosine similarity into a log-likelihood. Lower means the
    # observation dominates; higher lets the transition model smooth more.
    TEMPERATURE = 0.06
    # How many onsets ahead a single transition may skip, covering onsets
    # too quiet or too brief to register.
    MAX_SKIP = 2
    # Extra cost per additional onset skipped, so skipping stays available
    # but never free.
    SKIP_PENALTY = 4.0
    # Floor on any state's score, as a log-probability offset from the best
    # path. It has to be far deeper than one frame's observation range
    # (~1/TEMPERATURE), or a distant state with a good match leapfrogs the
    # whole Viterbi path in a single frame -- which is precisely how an
    # earlier, shallower floor let this tracker jump hundreds of onsets.
    # Deep enough that recovery needs sustained evidence, not one lucky frame.
    MIN_LOG_SCORE = -80.0

    def __init__(self, timeline: list[TimelineOnset], config: TrackerConfig | None = None):
        super().__init__(timeline, config)
        self._log_delta: np.ndarray | None = None
        self._expected_frames: np.ndarray | None = None

    def _init_state(self, frame_seconds: float) -> None:
        count = len(self.timeline)
        # Expected dwell in frames, from each onset's own length.
        self._expected_frames = np.maximum(
            1.0, np.array([o.advance_seconds for o in self.timeline]) / max(frame_seconds, 1e-6)
        )
        # Start committed to the top of the piece.
        self._log_delta = np.full(count, self.MIN_LOG_SCORE)
        self._log_delta[0] = 0.0

    def observe(self, frame: np.ndarray) -> int:
        if _is_silent(frame):
            return self.position

        frame_seconds = len(frame) / SAMPLE_RATE
        if self._log_delta is None:
            self._init_state(frame_seconds)
        assert self._log_delta is not None and self._expected_frames is not None

        observed = self._salience(self._analysis_window(frame))
        if not observed.any():
            return self.position

        log_obs = (self._templates @ observed) / self.TEMPERATURE

        # Transition step: each state can be reached by staying put or by
        # advancing up to MAX_SKIP onsets. Staying costs log(1 - 1/dwell),
        # advancing costs log(1/dwell) with a further penalty per onset
        # skipped, so skipping is available but never free.
        advance_prob = 1.0 / self._expected_frames
        log_stay = np.log(np.clip(1.0 - advance_prob, 1e-6, 1.0))
        log_advance = np.log(np.clip(advance_prob, 1e-6, 1.0))

        best = self._log_delta + log_stay
        for skip in range(1, self.MAX_SKIP + 1):
            shifted = np.full_like(best, -np.inf)
            shifted[skip:] = self._log_delta[:-skip] + log_advance[:-skip] - (skip - 1) * self.SKIP_PENALTY
            best = np.maximum(best, shifted)

        self._log_delta = best + log_obs
        self._log_delta -= self._log_delta.max()
        np.maximum(self._log_delta, self.MIN_LOG_SCORE, out=self._log_delta)

        self.position = int(np.argmax(self._log_delta))
        return self.position


METHODS: dict[str, type[BaseTracker]] = {
    MelodyPitchTracker.name: MelodyPitchTracker,
    ChromaTemplateTracker.name: ChromaTemplateTracker,
    SalienceTemplateTracker.name: SalienceTemplateTracker,
    HmmSalienceTracker.name: HmmSalienceTracker,
}

DEFAULT_METHOD = HmmSalienceTracker.name


def build_tracker(method: str, timeline: list[TimelineOnset], config: TrackerConfig | None = None) -> BaseTracker:
    if method not in METHODS:
        raise ValueError(f"Unknown tracking method {method!r}; known: {sorted(METHODS)}")
    return METHODS[method](timeline, config)
