"""Turning an exact score timeline into a flawed human performance.

Audio synthesized straight from the score is an unrealistically easy test:
the notes are exactly right and arrive exactly on the beat, so a tracker
can score well while being helpless against real playing. This module
introduces the specific ways a performance departs from its score, so
tracking can be measured against each failure mode separately rather than
against one vague "sloppy" recording.

Every degradation is seeded, so a given configuration always produces the
same performance and a change in tracking accuracy is attributable to the
tracker rather than to new random noise.

Timing changes move the ground truth with them: if the performer plays a
passage at half speed, the correct answer at a given second changes too.
`onset_times` records when each score onset was *actually* played, which is
what evaluation compares against.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from app.services.score_timeline import TimelineOnset


@dataclass
class PerformanceConfig:
    seed: int = 0

    # Constant tempo error: >1 plays faster than the score's marked tempo.
    tempo_scale: float = 1.0
    # Gradual drift by the end of the piece, as a fraction of tempo. 0.2
    # means finishing 20% faster than starting -- the usual unaccompanied
    # tendency to rush.
    tempo_drift: float = 0.0
    # Sinusoidal push-and-pull (rubato), as a fraction of tempo, plus how
    # many times it cycles over the piece.
    rubato_depth: float = 0.0
    rubato_cycles: float = 4.0
    # Random per-onset timing error, in seconds, applied as a standard
    # deviation. Models unsteady rather than merely fast or slow playing.
    timing_jitter_seconds: float = 0.0

    # Fraction of notes played at the wrong pitch (a semitone or two off,
    # the way a real mistake lands, not a random pitch).
    wrong_note_rate: float = 0.0
    # Fraction of notes not played at all.
    missed_note_rate: float = 0.0
    # Extra notes per played note, struck alongside as a fumble.
    extra_note_rate: float = 0.0

    # Additive white noise, relative to peak signal amplitude.
    noise_level: float = 0.0


@dataclass
class PerformanceNote:
    onset_index: int
    start_seconds: float
    duration_seconds: float
    pitch: str


@dataclass
class SimulatedPerformance:
    notes: list[PerformanceNote] = field(default_factory=list)
    # When each score onset was actually played. Present even for onsets
    # whose notes were all missed -- the performer still passed through
    # that point, so it is still the correct answer at that moment.
    onset_times: list[float] = field(default_factory=list)
    config: PerformanceConfig = field(default_factory=PerformanceConfig)


_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _shift_pitch(pitch: str, semitones: int) -> str:
    name, octave = pitch[:-1], int(pitch[-1])
    midi = (octave + 1) * 12 + _NOTE_NAMES.index(name) + semitones
    return f"{_NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def _tempo_multiplier(config: PerformanceConfig, progress: float) -> float:
    """Instantaneous tempo at `progress` (0..1) through the piece."""
    multiplier = config.tempo_scale
    multiplier *= 1.0 + config.tempo_drift * progress
    if config.rubato_depth:
        multiplier *= 1.0 + config.rubato_depth * math.sin(2 * math.pi * config.rubato_cycles * progress)
    return max(multiplier, 0.05)


def simulate(timeline: list[TimelineOnset], config: PerformanceConfig | None = None) -> SimulatedPerformance:
    config = config or PerformanceConfig()
    rng = random.Random(config.seed)
    performance = SimulatedPerformance(config=config)

    if not timeline:
        return performance

    # Walk the score accumulating real elapsed time, so tempo changes
    # compound the way they do in a performance rather than being applied
    # to the score's original absolute times.
    clock = 0.0
    for index, onset in enumerate(timeline):
        progress = index / max(len(timeline) - 1, 1)
        multiplier = _tempo_multiplier(config, progress)

        played_at = clock
        if config.timing_jitter_seconds:
            played_at += rng.gauss(0.0, config.timing_jitter_seconds)
        # Jitter must not reorder onsets; a performance that plays note 5
        # before note 4 is a different problem from an unsteady one.
        played_at = max(played_at, performance.onset_times[-1] + 1e-3 if performance.onset_times else 0.0)
        performance.onset_times.append(played_at)

        for note, duration in zip(onset.notes, onset.note_durations):
            if rng.random() < config.missed_note_rate:
                continue

            pitch = note.pitch
            if rng.random() < config.wrong_note_rate:
                pitch = _shift_pitch(pitch, rng.choice([-2, -1, 1, 2]))

            scaled = duration / multiplier
            performance.notes.append(PerformanceNote(index, played_at, scaled, pitch))

            if rng.random() < config.extra_note_rate:
                performance.notes.append(
                    PerformanceNote(index, played_at, scaled, _shift_pitch(note.pitch, rng.choice([-4, -3, 3, 4])))
                )

        clock += onset.advance_seconds / multiplier

    return performance


PRESETS: dict[str, PerformanceConfig] = {
    "clean": PerformanceConfig(),
    "fast": PerformanceConfig(tempo_scale=1.25),
    "slow": PerformanceConfig(tempo_scale=0.8),
    "rushing": PerformanceConfig(tempo_drift=0.30),
    "rubato": PerformanceConfig(rubato_depth=0.25, rubato_cycles=6),
    "unsteady": PerformanceConfig(timing_jitter_seconds=0.06),
    "wrong-notes": PerformanceConfig(wrong_note_rate=0.10),
    "missed-notes": PerformanceConfig(missed_note_rate=0.15),
    "extra-notes": PerformanceConfig(extra_note_rate=0.10),
    "noisy": PerformanceConfig(noise_level=0.08),
    # What an actual amateur run-through looks like: a bit fast, drifting
    # faster, unsteady, with a scattering of every kind of note error.
    "realistic": PerformanceConfig(
        tempo_scale=1.1,
        tempo_drift=0.15,
        rubato_depth=0.08,
        timing_jitter_seconds=0.04,
        wrong_note_rate=0.05,
        missed_note_rate=0.08,
        extra_note_rate=0.04,
        noise_level=0.04,
    ),
    # Deliberately past what should be trackable, to see where it breaks.
    "sloppy": PerformanceConfig(
        tempo_scale=1.2,
        tempo_drift=0.25,
        rubato_depth=0.15,
        timing_jitter_seconds=0.10,
        wrong_note_rate=0.12,
        missed_note_rate=0.18,
        extra_note_rate=0.10,
        noise_level=0.10,
    ),
}
