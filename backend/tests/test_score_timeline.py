from app.models import NoteBoundingBox
from app.services.score_timeline import (
    build_timeline,
    group_into_onsets,
    note_beats,
    onset_at,
)


def note(pitch: str, x: float, note_type: str = "quarter", measure: int = 1, y: float = 0.0) -> NoteBoundingBox:
    return NoteBoundingBox(
        x=x, y=y, width=10, height=10, note=note_type, pitch=pitch, measureIndex=measure, pageIndex=0
    )


def test_note_beats_known_and_unknown_types():
    assert note_beats("16th") == 0.25
    assert note_beats("whole") == 4.0
    # Unknown/missing types fall back to a quarter rather than blowing up.
    assert note_beats("bogus") == 1.0


def test_groups_near_identical_x_into_one_onset():
    # OMR gives chord noteheads ~1.5px of x jitter; those are one onset.
    notes = [note("C4", 100.0), note("E4", 101.5, y=20), note("G4", 100.8, y=40)]

    groups = group_into_onsets(notes)

    assert len(groups) == 1
    assert sorted(n.pitch for n in groups[0]) == ["C4", "E4", "G4"]


def test_separates_distinct_onsets():
    notes = [note("C4", 100.0), note("D4", 150.0), note("E4", 200.0)]

    groups = group_into_onsets(notes)

    assert [g[0].pitch for g in groups] == ["C4", "D4", "E4"]


def test_does_not_group_across_measures_even_at_similar_x():
    # x restarts on each system, so the same x in a later measure is a
    # different moment, not the same onset.
    notes = [note("C4", 100.0, measure=1), note("D4", 100.0, measure=2)]

    assert len(group_into_onsets(notes)) == 2


def test_timeline_advances_by_the_shortest_note_in_each_onset():
    # A held whole note in the bass under a moving eighth in the melody:
    # the next onset should arrive after the eighth, not the whole.
    notes = [
        note("C2", 100.0, "whole"),
        note("C5", 101.0, "eighth", y=50),
        note("D5", 150.0, "eighth", y=50),
    ]

    timeline = build_timeline(notes, tempo_bpm=60.0)  # 1 beat == 1 second

    assert len(timeline) == 2
    assert timeline[0].start_seconds == 0.0
    assert timeline[0].advance_seconds == 0.5  # the eighth, not the whole
    assert timeline[1].start_seconds == 0.5
    # The whole note still sounds for its own full length.
    assert max(timeline[0].note_durations) == 4.0


def test_timeline_start_times_accumulate_at_tempo():
    notes = [note("C4", 100.0, "quarter"), note("D4", 150.0, "quarter"), note("E4", 200.0, "quarter")]

    timeline = build_timeline(notes, tempo_bpm=120.0)  # quarter == 0.5s

    assert [round(o.start_seconds, 3) for o in timeline] == [0.0, 0.5, 1.0]


def test_onset_at_returns_the_currently_sounding_onset():
    notes = [note("C4", 100.0, "quarter"), note("D4", 150.0, "quarter")]
    timeline = build_timeline(notes, tempo_bpm=60.0)

    assert onset_at(timeline, 0.0).pitches == ["C4"]
    assert onset_at(timeline, 0.99).pitches == ["C4"]
    assert onset_at(timeline, 1.0).pitches == ["D4"]
    assert onset_at(timeline, 99.0).pitches == ["D4"]  # past the end, still the last


def test_onset_at_before_the_first_onset_is_none():
    timeline = build_timeline([note("C4", 100.0)], tempo_bpm=60.0)

    assert onset_at(timeline, -1.0) is None
