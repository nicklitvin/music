"""The live score-following path: LIVE_CONFIG + the reported-position rate
limiter from app.routers.audio_ws, exercised end to end on synthesized
audio the way /ws/track-audio drives it.

Real-recording accuracy (including finding a cold start from anywhere on
the page) is covered by app/services/benchmark_eval.py's
`evaluate_start_points`. What matters here is steadiness: once the tracker
is confidently locked onto a position -- whether seeded there explicitly or
found from audio alone -- the highlight must not lurch across the score on
a single fooled frame.
"""

import numpy as np
import pytest

from app.models import NoteBoundingBox
from app.routers.audio_ws import LIVE_CONFIG, ReportedPosition
from app.services.note_estimation import midi_to_hz, midi_to_pitch, pitch_to_midi
from app.services.position_markov import MarkovPositionTracker
from app.services.score_timeline import build_timeline

SAMPLE_RATE = 16000
FRAME = 1200
FRAMES_PER_ONSET = 7  # ~0.5s per onset, matching tempo 120 quarters

# 30 onsets, each a bare fifth, roots walking up by a semitone from C3.
# No exact repeats, so every position is identifiable and a wrong lock is a
# genuine failure rather than an ambiguity.
ROOTS = [pitch_to_midi("C3") + i for i in range(30)]
SEQUENCE = [[midi_to_pitch(root), midi_to_pitch(root + 7)] for root in ROOTS]


def timeline():
    notes: list[NoteBoundingBox] = []
    for index, chord in enumerate(SEQUENCE):
        for offset, pitch in enumerate(chord):
            notes.append(
                NoteBoundingBox(
                    x=100.0 + 50 * index + offset * 0.5, y=0, width=10, height=10,
                    note="quarter", pitch=pitch, measureIndex=1, pageIndex=0,
                )
            )
    return build_timeline(notes, tempo_bpm=120.0)


def render(chords, frames_each=FRAMES_PER_ONSET):
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


def live_positions(audio, start_index=0):
    """What the client would receive: rate-limited onset indices, one per frame."""
    tracker = MarkovPositionTracker(timeline(), LIVE_CONFIG)
    tracker.apply_hint(start_index, strength=0.9, width=3.0)
    reported = ReportedPosition(index=start_index)
    out = []
    for start in range(0, len(audio) - FRAME, FRAME):
        estimate = tracker.observe(audio[start : start + FRAME])
        out.append(reported.update(estimate.index))
    return out


# --- the rate limiter in isolation ---------------------------------------


def test_small_forward_steps_pass_through():
    reported = ReportedPosition(index=0, step=2)
    assert [reported.update(t) for t in (1, 2, 4, 5)] == [1, 2, 4, 5]


def test_a_single_far_target_cannot_teleport_the_highlight():
    reported = ReportedPosition(index=5, step=2, confirm=8)
    # One frame says "onset 200"; the highlight creeps, it does not jump.
    assert reported.update(200) == 7
    assert reported.update(6) == 6  # and snaps back when the belief returns


def test_a_consistently_reported_jump_is_eventually_taken():
    reported = ReportedPosition(index=0, step=2, confirm=4)
    results = [reported.update(50) for _ in range(4)]
    assert results[-1] == 50  # a real restart / skipped repeat still lands


def test_set_moves_immediately():
    reported = ReportedPosition(index=10)
    reported.set(90)
    assert reported.index == 90


def test_backward_creep_is_capped():
    reported = ReportedPosition(index=20, back=3, confirm=8)
    assert reported.update(0) == 17


# --- end to end on synthesized audio -----------------------------------


def test_playthrough_from_the_top_never_lurches():
    positions = live_positions(render(SEQUENCE), start_index=0)

    jumps = np.abs(np.diff(positions))
    assert jumps.max() <= 3, f"largest single-frame move was {jumps.max()}"
    assert positions[-1] >= len(SEQUENCE) - 3  # arrived at the end


@pytest.mark.parametrize("start_at", [6, 12, 18, 24])
def test_drop_in_partway_locks_on_without_lurching(start_at):
    # Belief is seeded at the top (that is what "start tracking" means), but
    # the audio actually begins partway through the first page.
    audio = render(SEQUENCE[start_at:])
    positions = live_positions(audio, start_index=0)

    # Reaches the true position...
    true_end = len(SEQUENCE) - 1
    assert abs(positions[-1] - true_end) <= 2
    # ...and once it has caught up, stays smooth.
    settled = positions[len(positions) // 2 :]
    assert np.abs(np.diff(settled)).max() <= 3


def test_a_quiet_moment_before_playing_starts_does_not_advance_the_highlight():
    # The reported bug: press "Start Tracking", and before any real playing
    # a brief window of room noise / mic settling gets fed in. It must not
    # read as "the piece has started" and creep the highlight forward --
    # only a real struck note should move it off the top.
    rng = np.random.default_rng(0)
    tracker = MarkovPositionTracker(timeline(), LIVE_CONFIG)
    tracker.apply_hint(0, strength=0.9, width=3.0)
    reported = ReportedPosition(index=0)

    positions = []
    for _ in range(100):  # 7.5s of nothing but noise
        estimate = tracker.observe(rng.normal(0, 2500, FRAME))
        positions.append(reported.update(estimate.index))

    assert max(positions) <= 2, f"quiet noise alone reached onset {max(positions)}"

    # Once the player actually starts, tracking still works normally.
    audio = render(SEQUENCE)
    final = [reported.update(tracker.observe(audio[s : s + FRAME]).index)
             for s in range(0, len(audio) - FRAME, FRAME)][-1]
    assert final >= len(SEQUENCE) - 3


def test_locality_window_blocks_a_jump_to_an_identical_passage_far_away():
    # Block A at the very start, the identical block A again ~90 onsets
    # later, unrelated filler between. Playing block A must hold the
    # highlight at the start -- the far copy is outside the locality window
    # and cannot win no matter how well it matches.
    block_a = [[p] for p in ("C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5")]
    filler = [[midi_to_pitch(pitch_to_midi("C3") + (i % 24))] for i in range(80)]
    sequence = block_a + filler + block_a

    notes: list[NoteBoundingBox] = []
    for index, chord in enumerate(sequence):
        for pitch in chord:
            notes.append(
                NoteBoundingBox(
                    x=100.0 + 50 * index, y=0, width=10, height=10,
                    note="quarter", pitch=pitch, measureIndex=1, pageIndex=0,
                )
            )
    tl = build_timeline(notes, tempo_bpm=120.0)

    tracker = MarkovPositionTracker(tl, LIVE_CONFIG)
    tracker.apply_hint(0, strength=0.9, width=3.0)
    reported = ReportedPosition(index=0)
    audio = render(block_a, frames_each=10)
    positions = [
        reported.update(tracker.observe(audio[s : s + FRAME]).index)
        for s in range(0, len(audio) - FRAME, FRAME)
    ]

    far_copy_starts_at = len(block_a) + len(filler)
    assert max(positions) < far_copy_starts_at - LIVE_CONFIG.search_ahead
