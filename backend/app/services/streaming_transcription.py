"""Note detection by learned transcription, over a live audio stream.

Replaces the hand-written harmonic-salience estimator
(note_estimation.py) as the evidence source for position tracking. On the
start-anywhere benchmark this is worth a lot: 40% -> 57% of start points
found within 5s, and 88% -> 96% found at all (scripts/compare_front_ends.py).

The model is Spotify's basic-pitch (Apache 2.0, vendored under
app/assets/basic_pitch/ with its LICENSE/NOTICE -- it is 230KB and takes
raw audio, the CQT front end being inside the graph, so nothing beyond
onnxruntime is needed and the `basic-pitch` package itself is not a
dependency of this app).

Streaming is not how basic-pitch is normally run -- `predict()` takes a
whole file -- so this mirrors its windowing exactly, because that
windowing is load-bearing for quality:

  * the model consumes 43844-sample (1.99s) windows at 22.05kHz,
  * each producing 172 frames, of which the first and last 15 are thrown
    away, since a convolutional model has no context beyond the window
    edge and those frames are measurably worse.

Discarding the trailing 15 frames is what costs latency: a frame is only
emitted once ~174ms of *following* audio exists. That is the price of
matching offline quality, and it is well inside a page-turner's budget.
Inference then runs every `advance_seconds` of new audio rather than every
frame, which is what keeps this affordable -- a window costs ~55ms of CPU,
so the default advance is ~18% of one core per session.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

# basic-pitch's own constants -- the model is fixed to these.
MODEL_SAMPLE_RATE = 22050
MODEL_WINDOW_SAMPLES = 43844
MODEL_FFT_HOP = 256
MODEL_FRAMES_PER_WINDOW = 172
# Frames trimmed from each end of a window (basic-pitch's n_overlapping_frames / 2).
TRIM_FRAMES = 15
MODEL_PATH = Path(__file__).resolve().parent.parent / "assets" / "basic_pitch" / "nmp.onnx"

# The model's posteriorgram has a dense, low-valued noise floor; squaring
# before unit-norming sharpens the contrast between real notes and it.
# Swept against the benchmark -- 1.0 and 3.0 are both measurably worse.
SHARPEN = 2.0

# How much of the evidence is note *attacks* rather than sustained pitch.
# The tracker's templates describe what is ringing at a score position,
# which is blurry by construction (a held chord looks the same for a
# second); where notes are struck is what pins a position down. 0.2-0.5 is
# a plateau on the benchmark, 0 (sustain only) is ~5 points worse.
ATTACK_WEIGHT = 0.35


# basic-pitch's own file-mode windowing: windows advance by this much, and
# the frames trimmed from each end are what the overlap covers.
MODEL_WINDOW_ADVANCE = MODEL_WINDOW_SAMPLES - 2 * TRIM_FRAMES * MODEL_FFT_HOP


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vector)
    return vector / norm if norm > 0 else vector


def transcribe_whole(audio: np.ndarray, input_rate: int, session=None) -> tuple[np.ndarray, np.ndarray]:
    """Run the model over a whole signal, the way basic-pitch's own
    `predict()` does: fixed windows on a common frame grid, trimming the
    context-starved frames at each end. Returns (note, onset) at
    MODEL_FRAME_RATE.

    Offline counterpart to StreamingTranscriber, sharing its model and
    constants so the benchmark can compare like with like.
    """
    session = session or load_session()
    signal = resample_to_model_rate(np.asarray(audio, dtype=np.float32), input_rate)
    # Pad the head so the first real sample sits past the frames that get
    # trimmed, and the tail so the final window is whole.
    signal = np.concatenate([np.zeros(TRIM_FRAMES * MODEL_FFT_HOP, dtype=np.float32), signal])
    notes, onsets = [], []
    for start in range(0, max(1, len(signal)), MODEL_WINDOW_ADVANCE):
        window = signal[start : start + MODEL_WINDOW_SAMPLES]
        if len(window) < MODEL_WINDOW_SAMPLES:
            window = np.pad(window, (0, MODEL_WINDOW_SAMPLES - len(window)))
        note, onset = session.run(
            ["StatefulPartitionedCall:1", "StatefulPartitionedCall:2"],
            {"serving_default_input_2:0": window.reshape(1, MODEL_WINDOW_SAMPLES, 1)},
        )
        notes.append(note[0][TRIM_FRAMES:-TRIM_FRAMES])
        onsets.append(onset[0][TRIM_FRAMES:-TRIM_FRAMES])
    return np.concatenate(notes), np.concatenate(onsets)


def load_session():
    """An onnxruntime session for the vendored model. Cheap to keep around
    and safe to share across requests (ORT sessions are thread-safe)."""
    import onnxruntime as ort

    options = ort.SessionOptions()
    # One session may be shared by several live sockets; let ORT use a
    # single thread per run so concurrent sessions don't fight each other.
    options.intra_op_num_threads = 1
    return ort.InferenceSession(str(MODEL_PATH), options, providers=["CPUExecutionProvider"])


def combined_salience(note_frame: np.ndarray, onset_frame: np.ndarray, attack_weight: float = ATTACK_WEIGHT) -> np.ndarray:
    """One frame of evidence: sustained pitch and note attacks concatenated.

    Paired with `combined_templates`, the tracker's existing dot product
    against the templates becomes the sum of both matches.
    """
    sustain = _unit(note_frame**SHARPEN)
    attack = _unit(onset_frame**SHARPEN)
    return np.concatenate([sustain * (1.0 - attack_weight), attack * attack_weight])


def combined_templates(timeline, attack_weight: float = ATTACK_WEIGHT) -> np.ndarray:
    """Templates matching `combined_salience`: what is ringing at each score
    onset, concatenated with what that onset strikes."""
    from app.services.note_estimation import MAX_MIDI, MIN_MIDI, pitch_to_midi
    from app.services.position_markov import build_templates

    sustain = build_templates(timeline)
    struck = np.zeros_like(sustain)
    for index, onset in enumerate(timeline):
        for note in onset.notes:
            midi = pitch_to_midi(note.pitch)
            if midi and MIN_MIDI <= midi <= MAX_MIDI:
                struck[index, midi - MIN_MIDI] = 1.0
        struck[index] = _unit(struck[index])
    return np.hstack([sustain * (1.0 - attack_weight), struck * attack_weight])


class StreamingTranscriber:
    """Feed it consecutive PCM frames; it yields per-hop salience vectors.

    `hop_samples` is the caller's frame size in *its* sample rate, and the
    output is one salience vector per such hop, so the tracker sees exactly
    the frame cadence it always has. Frames come out in bursts (one per
    inference), late by the model's trailing-context requirement.
    """

    def __init__(
        self,
        session=None,
        input_rate: int = 16000,
        hop_samples: int = 1200,
        advance_seconds: float = 0.3,
        attack_weight: float = ATTACK_WEIGHT,
        margin_frames: int = TRIM_FRAMES,
    ):
        self.session = session if session is not None else load_session()
        self.input_rate = input_rate
        self.hop_samples = hop_samples
        self.attack_weight = attack_weight
        self.advance_samples = max(hop_samples, int(advance_seconds * input_rate))
        # How far back from the window edge a frame must sit to be emitted.
        # TRIM_FRAMES is basic-pitch's own minimum; more trailing context
        # costs latency and buys accuracy, which is what makes this worth
        # tuning rather than fixing (see the benchmark in
        # scripts/compare_front_ends.py --streaming).
        self.margin_frames = margin_frames

        self._buffer = np.zeros(0, dtype=np.float32)
        # basic-pitch pads the head of the signal so the first real sample
        # sits mid-window rather than at the edge it trims.
        self._lead_in = int(TRIM_FRAMES * MODEL_FFT_HOP * input_rate / MODEL_SAMPLE_RATE)
        self._buffer = np.zeros(self._lead_in, dtype=np.float32)
        self._emitted_hops = 0
        self._consumed_before_buffer = 0

    @property
    def _window_input_samples(self) -> int:
        return int(np.ceil(MODEL_WINDOW_SAMPLES * self.input_rate / MODEL_SAMPLE_RATE))

    def push(self, pcm: np.ndarray) -> list[np.ndarray]:
        """Append audio (any length). Returns salience for whichever whole
        hops have become available, in order."""
        self._buffer = np.concatenate([self._buffer, np.asarray(pcm, dtype=np.float32)])
        out: list[np.ndarray] = []
        while True:
            emitted = self._try_infer()
            if not emitted:
                return out
            out.extend(emitted)

    def _try_infer(self) -> list[np.ndarray]:
        window_samples = self._window_input_samples
        if len(self._buffer) < window_samples:
            return []  # not enough audio for a full window yet
        available = self._consumed_before_buffer + len(self._buffer)
        # A frame is only trustworthy once enough *following* audio exists,
        # so the newest hop we may emit ends that far back.
        usable_end = available - int(self.margin_frames * MODEL_FFT_HOP * self.input_rate / MODEL_SAMPLE_RATE)
        ready_hops = usable_end // self.hop_samples
        if ready_hops <= self._emitted_hops:
            return []
        # Wait until enough new audio has accrued to be worth an inference;
        # without this a window is recomputed on every 75ms frame.
        if (ready_hops - self._emitted_hops) * self.hop_samples < self.advance_samples:
            return []

        window = self._buffer[-window_samples:]
        window_start = self._consumed_before_buffer + len(self._buffer) - window_samples
        note, onset = self._run(window)

        out: list[np.ndarray] = []
        for hop in range(self._emitted_hops, ready_hops):
            hop_start = hop * self.hop_samples
            hop_end = hop_start + self.hop_samples
            lo = self._model_frame(hop_start - window_start)
            hi = max(lo + 1, self._model_frame(hop_end - window_start))
            lo = max(0, min(lo, MODEL_FRAMES_PER_WINDOW - 1))
            hi = max(lo + 1, min(hi, MODEL_FRAMES_PER_WINDOW))
            out.append(combined_salience(note[lo:hi].max(axis=0), onset[lo:hi].max(axis=0), self.attack_weight))
        self._emitted_hops = ready_hops

        # Drop audio no longer needed for the next window.
        keep = window_samples
        if len(self._buffer) > keep:
            self._consumed_before_buffer += len(self._buffer) - keep
            self._buffer = self._buffer[-keep:]
        return out

    def _model_frame(self, offset_input_samples: int) -> int:
        seconds = offset_input_samples / self.input_rate
        return int(round(seconds * MODEL_SAMPLE_RATE / MODEL_FFT_HOP))

    def _run(self, window_input: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        audio = resample_to_model_rate(window_input, self.input_rate)
        if len(audio) < MODEL_WINDOW_SAMPLES:
            audio = np.pad(audio, (0, MODEL_WINDOW_SAMPLES - len(audio)))
        audio = audio[:MODEL_WINDOW_SAMPLES].astype(np.float32)
        note, onset = self.session.run(
            ["StatefulPartitionedCall:1", "StatefulPartitionedCall:2"],
            {"serving_default_input_2:0": audio.reshape(1, MODEL_WINDOW_SAMPLES, 1)},
        )
        return note[0], onset[0]


def resample_to_model_rate(audio: np.ndarray, input_rate: int) -> np.ndarray:
    """Resample a whole window at once rather than streaming a filter, so
    there is no resampler state to carry across chunk boundaries (and so no
    discontinuity where chunks meet)."""
    if input_rate == MODEL_SAMPLE_RATE:
        return np.asarray(audio, dtype=np.float32)
    from math import gcd

    divisor = gcd(MODEL_SAMPLE_RATE, input_rate)
    from scipy.signal import resample_poly

    return resample_poly(audio, MODEL_SAMPLE_RATE // divisor, input_rate // divisor).astype(np.float32)
