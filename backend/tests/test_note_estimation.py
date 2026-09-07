import numpy as np
import pytest

from app.services.note_estimation import (
    MIN_MIDI,
    NUM_PITCHES,
    HarmonicSalienceEstimator,
    is_silent,
    midi_to_hz,
    midi_to_pitch,
    pitch_to_midi,
)

SAMPLE_RATE = 16000
FRAME = 1200


def tone(pitches: list[str], seconds: float = 0.5, harmonics: tuple[float, ...] = (1.0, 0.5, 0.25)) -> np.ndarray:
    t = np.arange(int(SAMPLE_RATE * seconds)) / SAMPLE_RATE
    out = np.zeros_like(t)
    for pitch in pitches:
        hz = midi_to_hz(pitch_to_midi(pitch))
        for index, amplitude in enumerate(harmonics, start=1):
            out += amplitude * np.sin(2 * np.pi * hz * index * t)
    return out / max(len(pitches), 1) * 8000


def run(estimator: HarmonicSalienceEstimator, audio: np.ndarray) -> np.ndarray:
    salience = np.zeros(NUM_PITCHES)
    for start in range(0, len(audio) - FRAME, FRAME):
        salience = estimator.estimate(audio[start : start + FRAME])
    return salience


def test_pitch_midi_round_trip():
    for pitch in ("A0", "C4", "F#5", "C8"):
        assert midi_to_pitch(pitch_to_midi(pitch)) == pitch


def test_midi_to_hz_concert_a():
    assert midi_to_hz(69) == pytest.approx(440.0)


def test_is_silent():
    assert is_silent(np.zeros(FRAME))
    assert not is_silent(tone(["C4"], seconds=0.1))


def test_silence_yields_no_evidence():
    estimator = HarmonicSalienceEstimator()
    assert not estimator.estimate(np.zeros(FRAME)).any()


def test_single_tone_peaks_at_that_pitch():
    estimator = HarmonicSalienceEstimator()
    salience = run(estimator, tone(["C4"]))

    assert MIN_MIDI + int(np.argmax(salience)) == pitch_to_midi("C4")


def test_harmonics_do_not_outrank_the_fundamental():
    # A harmonic-rich tone must not read as its own octave -- the failure
    # that made discrete peak-picking unreliable on real instruments.
    estimator = HarmonicSalienceEstimator()
    salience = run(estimator, tone(["C4"], harmonics=(1.0, 0.8, 0.6, 0.5)))

    fundamental = salience[pitch_to_midi("C4") - MIN_MIDI]
    octave_up = salience[pitch_to_midi("C5") - MIN_MIDI]
    assert fundamental > octave_up


def test_chord_shows_all_of_its_notes():
    estimator = HarmonicSalienceEstimator()
    salience = run(estimator, tone(["C4", "E4", "G4"]))

    peak = salience.max()
    for pitch in ("C4", "E4", "G4"):
        assert salience[pitch_to_midi(pitch) - MIN_MIDI] > peak * 0.3


def test_low_notes_are_distinguishable():
    # The reason the analysis window is longer than one frame: adjacent
    # bass semitones are only a couple of Hz apart.
    estimator = HarmonicSalienceEstimator()
    salience = run(estimator, tone(["E2"]))

    assert MIN_MIDI + int(np.argmax(salience)) == pitch_to_midi("E2")


def test_salience_is_unit_norm_when_there_is_signal():
    estimator = HarmonicSalienceEstimator()
    salience = run(estimator, tone(["C4"]))

    assert np.linalg.norm(salience) == pytest.approx(1.0)


def test_top_pitches_reports_the_played_notes():
    estimator = HarmonicSalienceEstimator()
    salience = run(estimator, tone(["C4", "G4"]))

    assert set(estimator.top_pitches(salience)) >= {"C4", "G4"}


def test_top_pitches_of_silence_is_empty():
    estimator = HarmonicSalienceEstimator()
    assert estimator.top_pitches(np.zeros(NUM_PITCHES)) == []
