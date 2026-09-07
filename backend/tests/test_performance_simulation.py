from app.models import NoteBoundingBox
from app.services.performance_simulation import PRESETS, PerformanceConfig, simulate
from app.services.score_timeline import build_timeline


def note(pitch: str, x: float, note_type: str = "quarter") -> NoteBoundingBox:
    return NoteBoundingBox(
        x=x, y=0, width=10, height=10, note=note_type, pitch=pitch, measureIndex=1, pageIndex=0
    )


def simple_timeline(count: int = 20):
    return build_timeline([note("C4", 100.0 + 50 * i) for i in range(count)], tempo_bpm=120.0)


def test_clean_performance_matches_the_score_exactly():
    timeline = simple_timeline()
    performance = simulate(timeline, PerformanceConfig())

    assert len(performance.notes) == len(timeline)
    for onset, played in zip(timeline, performance.onset_times):
        assert played == onset.start_seconds


def test_faster_tempo_shortens_the_performance():
    timeline = simple_timeline()

    fast = simulate(timeline, PerformanceConfig(tempo_scale=1.5))
    slow = simulate(timeline, PerformanceConfig(tempo_scale=0.5))

    assert fast.onset_times[-1] < slow.onset_times[-1]


def test_drift_makes_later_onsets_closer_together():
    timeline = simple_timeline(30)
    performance = simulate(timeline, PerformanceConfig(tempo_drift=0.5))

    times = performance.onset_times
    early_gap = times[2] - times[1]
    late_gap = times[-1] - times[-2]
    assert late_gap < early_gap


def test_onset_times_never_go_backwards_even_with_jitter():
    # Jitter should make the performance unsteady, not reorder the music.
    timeline = simple_timeline(40)
    performance = simulate(timeline, PerformanceConfig(timing_jitter_seconds=0.2, seed=3))

    times = performance.onset_times
    assert all(later > earlier for earlier, later in zip(times, times[1:]))


def test_missed_notes_drops_some_notes():
    timeline = simple_timeline(60)
    performance = simulate(timeline, PerformanceConfig(missed_note_rate=0.5, seed=1))

    assert 0 < len(performance.notes) < len(timeline)


def test_extra_notes_adds_some_notes():
    timeline = simple_timeline(60)
    performance = simulate(timeline, PerformanceConfig(extra_note_rate=0.5, seed=1))

    assert len(performance.notes) > len(timeline)


def test_wrong_notes_changes_pitches_but_not_count():
    timeline = simple_timeline(60)
    performance = simulate(timeline, PerformanceConfig(wrong_note_rate=0.5, seed=1))

    assert len(performance.notes) == len(timeline)
    assert any(played.pitch != "C4" for played in performance.notes)


def test_every_note_still_maps_back_to_a_score_onset():
    timeline = simple_timeline(30)
    performance = simulate(timeline, PRESETS["sloppy"])

    for played in performance.notes:
        assert 0 <= played.onset_index < len(timeline)


def test_onset_times_cover_every_onset_even_when_notes_are_missed():
    # The performer passes through an onset even if they fluff every note
    # in it, so it stays the correct answer at that moment.
    timeline = simple_timeline(30)
    performance = simulate(timeline, PerformanceConfig(missed_note_rate=1.0))

    assert performance.notes == []
    assert len(performance.onset_times) == len(timeline)


def test_simulation_is_reproducible_for_a_seed():
    timeline = simple_timeline(30)
    config = PerformanceConfig(wrong_note_rate=0.3, missed_note_rate=0.2, seed=7)

    first = simulate(timeline, config)
    second = simulate(timeline, config)

    assert [n.pitch for n in first.notes] == [n.pitch for n in second.notes]
    assert first.onset_times == second.onset_times


def test_different_seeds_give_different_performances():
    timeline = simple_timeline(40)
    a = simulate(timeline, PerformanceConfig(wrong_note_rate=0.3, seed=1))
    b = simulate(timeline, PerformanceConfig(wrong_note_rate=0.3, seed=2))

    assert [n.pitch for n in a.notes] != [n.pitch for n in b.notes]


def test_empty_timeline_is_handled():
    assert simulate([], PRESETS["sloppy"]).notes == []
