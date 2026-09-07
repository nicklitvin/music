import numpy as np
import pytest

from app.models import NoteBoundingBox
from app.services.position_tracking import (
    DEFAULT_METHOD,
    METHODS,
    SAMPLE_RATE,
    build_tracker,
    midi_to_hz,
    pitch_to_midi,
    sounding_weights,
)
from app.services.score_timeline import build_timeline

FRAME_SAMPLES = 1200  # 75ms, matching the live pipeline


def note(pitch: str, x: float, note_type: str = "quarter", measure: int = 1) -> NoteBoundingBox:
    return NoteBoundingBox(
        x=x, y=0, width=10, height=10, note=note_type, pitch=pitch, measureIndex=measure, pageIndex=0
    )


def render(pitches_over_time: list[list[str]], frames_each: int) -> np.ndarray:
    """Synthesizes consecutive chords as harmonic tones, one block each."""
    blocks = []
    for pitches in pitches_over_time:
        samples = np.zeros(FRAME_SAMPLES * frames_each)
        t = np.arange(len(samples)) / SAMPLE_RATE
        for pitch in pitches:
            hz = midi_to_hz(pitch_to_midi(pitch))
            for harmonic, amplitude in enumerate([1.0, 0.5, 0.25], start=1):
                samples += amplitude * np.sin(2 * np.pi * hz * harmonic * t)
        blocks.append(samples / max(len(pitches), 1) * 8000)
    return np.concatenate(blocks)


def test_pitch_to_midi_round_trips_known_values():
    assert pitch_to_midi("C4") == 60
    assert pitch_to_midi("A4") == 69
    assert pitch_to_midi("C#4") == 61
    assert pitch_to_midi("nonsense") is None


def test_midi_to_hz_concert_a():
    assert midi_to_hz(69) == pytest.approx(440.0)


def test_sounding_weights_includes_notes_still_held_from_earlier():
    # A whole note under two eighths: at the second onset the whole note is
    # still ringing, so it must appear in what is sounding.
    notes = [note("C2", 100.0, "whole"), note("C5", 101.0, "eighth"), note("D5", 150.0, "eighth")]
    timeline = build_timeline(notes, tempo_bpm=60.0)

    sounding = sounding_weights(timeline, 1)

    assert pitch_to_midi("C2") in sounding
    assert pitch_to_midi("D5") in sounding


def test_sounding_weights_drops_released_notes():
    notes = [note("C4", 100.0, "eighth"), note("D4", 150.0, "eighth")]
    timeline = build_timeline(notes, tempo_bpm=60.0)

    sounding = sounding_weights(timeline, 1)

    assert pitch_to_midi("C4") not in sounding  # released before the second onset
    assert pitch_to_midi("D4") in sounding


def test_sounding_weights_decays_older_notes():
    notes = [note("C2", 100.0, "whole"), note("C5", 101.0, "quarter"), note("D5", 150.0, "quarter")]
    timeline = build_timeline(notes, tempo_bpm=60.0)

    sounding = sounding_weights(timeline, 1)

    # The freshly struck note outweighs the one still ringing from before.
    assert sounding[pitch_to_midi("D5")] > sounding[pitch_to_midi("C2")]


@pytest.mark.parametrize("method", sorted(METHODS))
def test_every_tracker_reports_a_valid_position(method):
    notes = [note("C4", 100.0), note("E4", 150.0), note("G4", 200.0)]
    timeline = build_timeline(notes, tempo_bpm=120.0)
    tracker = build_tracker(method, timeline)

    audio = render([["C4"], ["E4"], ["G4"]], frames_each=4)
    for start in range(0, len(audio) - FRAME_SAMPLES, FRAME_SAMPLES):
        position = tracker.observe(audio[start : start + FRAME_SAMPLES])
        assert 0 <= position < len(timeline)


def test_build_tracker_rejects_unknown_method():
    timeline = build_timeline([note("C4", 100.0)])
    with pytest.raises(ValueError, match="Unknown tracking method"):
        build_tracker("no-such-method", timeline)


def test_default_method_follows_a_simple_sequence():
    # Four well-separated chords played in order; the default tracker should
    # end up at the last one rather than stalling or overshooting.
    pitches = [["C4", "E4"], ["D4", "F4"], ["E4", "G4"], ["F4", "A4"]]
    # Each chord gets its own x block, with the two notes close enough
    # together to land in one onset.
    notes = []
    for i, chord in enumerate(pitches):
        for j, pitch in enumerate(chord):
            notes.append(note(pitch, 100.0 + 50 * i + j * 0.5, "quarter"))

    timeline = build_timeline(notes, tempo_bpm=120.0)
    assert len(timeline) == 4

    tracker = build_tracker(DEFAULT_METHOD, timeline)
    audio = render(pitches, frames_each=7)  # 0.5s per chord at 75ms frames

    positions = []
    for start in range(0, len(audio) - FRAME_SAMPLES, FRAME_SAMPLES):
        positions.append(tracker.observe(audio[start : start + FRAME_SAMPLES]))

    assert positions[-1] == 3, f"expected to end on the last onset, got {positions[-1]}"
    # And it should never have run past the end of the score.
    assert max(positions) == 3


def test_tracker_position_never_stalls_at_zero_on_real_movement():
    pitches = [["C4"], ["G5"], ["C4"], ["G5"]]
    notes = [note(chord[0], 100.0 + 50 * i, "quarter") for i, chord in enumerate(pitches)]
    timeline = build_timeline(notes, tempo_bpm=120.0)

    tracker = build_tracker(DEFAULT_METHOD, timeline)
    audio = render(pitches, frames_each=7)
    positions = [
        tracker.observe(audio[s : s + FRAME_SAMPLES])
        for s in range(0, len(audio) - FRAME_SAMPLES, FRAME_SAMPLES)
    ]

    assert positions[-1] > 0
