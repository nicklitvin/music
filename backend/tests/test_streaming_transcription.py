"""The learned front end's silence cutoff, which follows the room."""

import numpy as np

from app.services import streaming_transcription as st

RATE = 16000
HOP = 1200


def hops_with_evidence(audio: np.ndarray) -> tuple[int, int]:
    transcriber = st.StreamingTranscriber()
    heard = total = 0
    for start in range(0, len(audio) - HOP + 1, HOP):
        for vector in transcriber.push(audio[start : start + HOP]):
            total += 1
            heard += bool(vector.any())
    return heard, total


def noise(seconds: float, rms: float, seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).normal(0, rms / 32768, int(seconds * RATE))


def chord(seconds: float, rms: float) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    tone = sum(np.sin(2 * np.pi * f * t) / (k + 1) for k, f in enumerate((220.0, 277.18, 329.63, 440.0)))
    return tone / np.sqrt(np.mean(tone**2)) * rms / 32768


def test_room_noise_is_silence():
    # The model reads notes into plain hiss; the cutoff must catch it even
    # where it is far quieter than the old fixed 500.
    heard, total = hops_with_evidence(noise(10, 80))
    assert total > 100 and heard == 0


def test_quiet_playing_in_a_quiet_room_is_heard():
    # The real failure: the browser delivered a piano at a median RMS of
    # ~165, and a fixed cutoff of 500 discarded almost all of it.
    audio = np.concatenate([noise(3, 5), chord(6, 150) + noise(6, 5, seed=1)])
    heard, total = hops_with_evidence(audio)
    assert heard > total * 0.5


def test_the_cutoff_stays_within_its_bounds():
    transcriber = st.StreamingTranscriber()
    assert transcriber.silence_rms == st.SILENCE_RMS  # nothing heard yet
    transcriber._hear_level(0.0)
    assert transcriber.silence_rms == st.QUIETEST_CUTOFF_RMS
    transcriber._hear_level(1.0)  # a loud hop never raises the floor at once
    assert transcriber.silence_rms == st.QUIETEST_CUTOFF_RMS
    for _ in range(100_000):
        transcriber._hear_level(1.0)
    assert transcriber.silence_rms == st.SILENCE_RMS  # never stricter than 500
