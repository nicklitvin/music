import numpy as np
import pytest

from app.models import NoteBoundingBox
from app.services.note_estimation import HarmonicSalienceEstimator, midi_to_hz, midi_to_pitch, pitch_to_midi
from app.services.position_markov import (
    MarkovConfig,
    MarkovPositionTracker,
    build_templates,
    sounding_weights,
)
from app.services.score_timeline import build_timeline

SAMPLE_RATE = 16000
FRAME = 1200

# A sequence with no repeated chords, so each position is identifiable on
# its own and a wrong lock is a real failure rather than an ambiguity.
SEQUENCE = [
    ["C4", "E4"],
    ["D4", "F4"],
    ["E4", "G4"],
    ["F4", "A4"],
    ["G4", "B4"],
    ["A4", "C5"],
]


def note(pitch: str, x: float, note_type: str = "quarter") -> NoteBoundingBox:
    return NoteBoundingBox(
        x=x, y=0, width=10, height=10, note=note_type, pitch=pitch, measureIndex=1, pageIndex=0
    )


def sequence_timeline():
    notes = []
    for index, chord in enumerate(SEQUENCE):
        for offset, pitch in enumerate(chord):
            notes.append(note(pitch, 100.0 + 50 * index + offset * 0.5))
    return build_timeline(notes, tempo_bpm=120.0)  # quarter == 0.5s


def render(chords, frames_each=7):
    blocks = []
    for chord in chords:
        t = np.arange(FRAME * frames_each) / SAMPLE_RATE
        block = np.zeros_like(t)
        for pitch in chord:
            hz = midi_to_hz(pitch_to_midi(pitch))
            for harmonic, amplitude in enumerate([1.0, 0.5, 0.25], start=1):
                block += amplitude * np.sin(2 * np.pi * hz * harmonic * t)
        blocks.append(block / len(chord) * 8000)
    return np.concatenate(blocks)


def feed(tracker, audio):
    estimates = []
    for start in range(0, len(audio) - FRAME, FRAME):
        estimates.append(tracker.observe(audio[start : start + FRAME]))
    return estimates


def test_sounding_weights_keeps_held_notes_and_drops_released_ones():
    notes = [note("C2", 100.0, "whole"), note("C5", 101.0, "eighth"), note("D5", 150.0, "eighth")]
    timeline = build_timeline(notes, tempo_bpm=60.0)

    sounding = sounding_weights(timeline, 1)

    assert pitch_to_midi("C2") in sounding  # whole note still ringing
    assert pitch_to_midi("D5") in sounding
    assert pitch_to_midi("C5") not in sounding  # eighth already released


def test_templates_are_unit_norm():
    templates = build_templates(sequence_timeline())
    assert np.allclose(np.linalg.norm(templates, axis=1), 1.0)


def test_starts_with_a_uniform_belief():
    timeline = sequence_timeline()
    tracker = MarkovPositionTracker(timeline)

    estimate = tracker.estimate()
    # Every onset equally likely, so confidence is only the small
    # neighbourhood around an arbitrary argmax -- nowhere near certain.
    assert estimate.confidence < 0.9
    probabilities = [p for _, p in estimate.candidates]
    assert max(probabilities) == pytest.approx(min(probabilities), rel=1e-6)


def test_finds_position_when_playback_starts_at_the_beginning():
    timeline = sequence_timeline()
    tracker = MarkovPositionTracker(timeline)

    estimates = feed(tracker, render(SEQUENCE))

    assert estimates[-1].index == len(SEQUENCE) - 1
    assert estimates[-1].confidence > 0.9


@pytest.mark.parametrize("start_at", [1, 2, 3, 4])
def test_finds_position_when_playback_starts_mid_score(start_at):
    # The point of the uniform prior: the player can drop in anywhere and
    # the model still works out where they are.
    timeline = sequence_timeline()
    tracker = MarkovPositionTracker(timeline)

    estimates = feed(tracker, render(SEQUENCE[start_at:]))

    # It should land on the final onset, having entered partway through.
    assert estimates[-1].index == len(SEQUENCE) - 1
    assert estimates[-1].confidence > 0.9


def test_confidence_rises_as_evidence_accumulates():
    timeline = sequence_timeline()
    tracker = MarkovPositionTracker(timeline)

    estimates = feed(tracker, render(SEQUENCE))

    assert estimates[0].confidence < estimates[-1].confidence


def test_confident_estimates_are_the_correct_ones():
    timeline = sequence_timeline()
    tracker = MarkovPositionTracker(timeline)
    frames_each = 7

    estimates = feed(tracker, render(SEQUENCE, frames_each))

    confident = [
        (i, e) for i, e in enumerate(estimates) if e.confidence > 0.95
    ]
    assert confident, "expected the model to become confident at some point"
    for frame_index, estimate in confident:
        expected = min(frame_index // frames_each, len(SEQUENCE) - 1)
        assert abs(estimate.index - expected) <= 1


def test_recovers_after_the_player_jumps_backwards():
    # A jump is exactly what the small uniform transition term exists for:
    # belief that has collapsed on the wrong onset must be able to move.
    timeline = sequence_timeline()
    tracker = MarkovPositionTracker(timeline)

    feed(tracker, render(SEQUENCE))  # play to the end
    assert tracker.estimate().index == len(SEQUENCE) - 1

    # Now restart from the top and keep playing.
    estimates = feed(tracker, render(SEQUENCE[:3], frames_each=14))

    assert estimates[-1].index == 2, "should have followed the jump back"


def test_candidates_are_sorted_most_likely_first():
    timeline = sequence_timeline()
    tracker = MarkovPositionTracker(timeline)
    estimate = feed(tracker, render(SEQUENCE[:2]))[-1]

    probabilities = [p for _, p in estimate.candidates]
    assert probabilities == sorted(probabilities, reverse=True)


def test_silence_does_not_change_the_belief():
    timeline = sequence_timeline()
    tracker = MarkovPositionTracker(timeline)
    feed(tracker, render(SEQUENCE[:2]))
    before = tracker.estimate()

    for _ in range(10):
        tracker.observe(np.zeros(FRAME))

    after = tracker.estimate()
    assert after.index == before.index
    assert after.confidence == pytest.approx(before.confidence)


def test_weak_undiscriminating_audio_does_not_move_the_belief_far():
    # Broadband noise: non-silent (passes the RMS gate), but its salience
    # peak rarely reaches evidence_floor, unlike a real struck note/chord
    # (test_evidence_floor_matches... below). A long run of it must not
    # scatter the tracker across the score -- not even via the transition
    # model's own advance, which is what let elapsed time alone look like
    # "the player has started" before anyone actually played. Small jump
    # probabilities (as tuned for live use, not the benchmark defaults) so
    # 200 frames of nothing but noise isn't just measuring how fast the
    # whole-score uniform-jump term diffuses on its own.
    notes = [note(midi_to_pitch(pitch_to_midi("C3") + i), 100.0 + 50 * i) for i in range(60)]
    timeline = build_timeline(notes, tempo_bpm=120.0)
    config = MarkovConfig(search_ahead=40, search_behind=10, jump_probability=1e-7, jump_probability_confident=1e-12)
    tracker = MarkovPositionTracker(timeline, config)
    tracker.apply_hint(0, strength=0.9, width=3.0)  # "start tracking" seeds belief at the top

    rng = np.random.default_rng(0)
    for _ in range(200):  # 15s of nothing but noise
        tracker.observe(rng.normal(0, 2500, FRAME))

    assert tracker.estimate().index <= 3


def test_evidence_floor_matches_the_salience_estimators_actual_scale():
    # A real chord's salience peak clears the floor; broadband noise at any
    # RMS does not (it spreads energy thinly instead of into harmonic
    # peaks). This is the calibration the floor/ceiling in MarkovConfig
    # assumes -- if the estimator's output shape ever changes, this is the
    # test that should catch a floor tuned for the wrong scale.
    estimator = HarmonicSalienceEstimator()
    salience = np.zeros(0)
    for chunk in render([["C4", "E4", "G4"]], frames_each=3).reshape(-1, FRAME):
        salience = estimator.estimate(chunk)
    assert salience.max() >= MarkovConfig().evidence_floor

    rng = np.random.default_rng(0)
    noisy_estimator = HarmonicSalienceEstimator()
    for _ in range(5):
        salience = noisy_estimator.estimate(rng.normal(0, 2500, FRAME))
    assert salience.max() < MarkovConfig().evidence_floor


def test_search_window_caps_how_far_one_frame_can_move_the_belief():
    # 60 distinct onsets. One frame of audio that matches a far onset: with
    # a tight window the belief cannot cross it in a single step; an
    # unconstrained tracker jumps straight to the match.
    notes = [note(midi_to_pitch(pitch_to_midi("C3") + i), 100.0 + 50 * i) for i in range(60)]
    timeline = build_timeline(notes, tempo_bpm=120.0)
    one_far_frame = render([[midi_to_pitch(pitch_to_midi("C3") + 45)]], frames_each=1)

    windowed = MarkovPositionTracker(timeline, MarkovConfig(search_ahead=6, search_behind=2))
    windowed.observe(one_far_frame)
    assert windowed.estimate().index <= 6

    free = MarkovPositionTracker(timeline, MarkovConfig())
    free.observe(one_far_frame)
    assert free.estimate().index >= 40


def test_jump_probability_of_zero_still_tracks_forward():
    timeline = sequence_timeline()
    tracker = MarkovPositionTracker(timeline, MarkovConfig(jump_probability=0.0))

    estimates = feed(tracker, render(SEQUENCE))

    assert estimates[-1].index == len(SEQUENCE) - 1


def test_hint_moves_a_confidently_wrong_tracker():
    # The case that motivates mixing rather than multiplying: once belief
    # has collapsed, the correct region's probability is ~0, and scaling
    # zero leaves zero.
    timeline = sequence_timeline()
    tracker = MarkovPositionTracker(timeline)
    feed(tracker, render(SEQUENCE))
    assert tracker.estimate().index == len(SEQUENCE) - 1
    assert tracker.estimate().confidence > 0.9

    estimate = tracker.apply_hint(0)

    assert estimate.index == 0


def test_hint_is_a_region_not_a_spike():
    timeline = sequence_timeline()
    tracker = MarkovPositionTracker(timeline)

    estimate = tracker.apply_hint(3)

    # Neighbours of the hinted onset keep real probability, because a
    # scroll says roughly where, not exactly which onset.
    nearby = dict(estimate.candidates)
    assert any(index != 3 and probability > 0.05 for index, probability in nearby.items())


def test_hint_lets_audio_take_over_again():
    timeline = sequence_timeline()
    tracker = MarkovPositionTracker(timeline)
    tracker.apply_hint(0)

    # Audio for the end of the sequence should still win over the hint.
    estimates = feed(tracker, render(SEQUENCE[4:], frames_each=14))

    assert estimates[-1].index == len(SEQUENCE) - 1


def test_hint_clamps_out_of_range_indices():
    timeline = sequence_timeline()
    tracker = MarkovPositionTracker(timeline)

    assert tracker.apply_hint(-50).index == 0
    assert tracker.apply_hint(9999).index == len(timeline) - 1


def test_hint_strength_zero_leaves_belief_alone():
    timeline = sequence_timeline()
    tracker = MarkovPositionTracker(timeline)
    feed(tracker, render(SEQUENCE[:3]))
    before = tracker.estimate().index

    assert tracker.apply_hint(len(timeline) - 1, strength=0.0).index == before
