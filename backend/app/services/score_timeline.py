"""Turns a flat list of note bounding boxes into a played-in-time timeline.

The OMR output (NoteBoundingBox list) has no timing -- only pitch, note
type ("eighth", "quarter", ...), measure number, and pixel position. To
synthesize audio from a score, and to know what "where the player is"
means at a given second, those notes have to be laid out on a time axis.

Reading order is: measure number, then left-to-right by x. Notes at
(nearly) the same x within a measure sound together as one chord -- OMR
gives chord noteheads slightly different x values (~1.5px of jitter, versus
>10px between genuinely separate onsets), hence ONSET_X_TOLERANCE.

Each onset advances the clock by the *shortest* note in it: in piano music
a long bass note is typically held under several shorter melody notes, so
the next onset arrives when the quickest voice in the current one ends.
This is a deliberate approximation -- it produces a plausible, deterministic
timeline suitable for generating test audio, not a faithful performance.
"""

from dataclasses import dataclass, field

from app.models import NoteBoundingBox

# Notes within this many pixels of each other (same measure) are treated as
# one simultaneous onset rather than consecutive ones.
ONSET_X_TOLERANCE = 8.0

# Note type -> duration in beats (a beat being one quarter note).
NOTE_TYPE_BEATS = {
    "64th": 0.0625,
    "32nd": 0.125,
    "16th": 0.25,
    "eighth": 0.5,
    "quarter": 1.0,
    "half": 2.0,
    "whole": 4.0,
}
DEFAULT_NOTE_BEATS = 1.0


@dataclass
class TimelineOnset:
    """One moment in the score: every note that starts sounding together."""

    index: int
    start_seconds: float
    # How long until the *next* onset (the shortest note here), which is not
    # necessarily how long each note in it sounds -- see `notes`/durations.
    advance_seconds: float
    notes: list[NoteBoundingBox] = field(default_factory=list)
    note_durations: list[float] = field(default_factory=list)

    @property
    def pitches(self) -> list[str]:
        return [note.pitch for note in self.notes]


def note_beats(note_type: str) -> float:
    return NOTE_TYPE_BEATS.get(note_type, DEFAULT_NOTE_BEATS)


def group_into_onsets(notes: list[NoteBoundingBox]) -> list[list[NoteBoundingBox]]:
    """Groups notes into simultaneous onsets, in reading order."""
    ordered = sorted(notes, key=lambda n: (n.pageIndex, n.measureIndex, n.x, n.y))

    groups: list[list[NoteBoundingBox]] = []
    for note in ordered:
        previous = groups[-1] if groups else None
        same_onset = (
            previous is not None
            and previous[0].pageIndex == note.pageIndex
            and previous[0].measureIndex == note.measureIndex
            and abs(note.x - previous[0].x) <= ONSET_X_TOLERANCE
        )
        if same_onset:
            previous.append(note)  # type: ignore[union-attr]
        else:
            groups.append([note])
    return groups


def build_timeline(notes: list[NoteBoundingBox], tempo_bpm: float = 99.0) -> list[TimelineOnset]:
    """Lays the score out in time at `tempo_bpm` quarter notes per minute."""
    seconds_per_beat = 60.0 / tempo_bpm

    timeline: list[TimelineOnset] = []
    clock = 0.0
    for index, group in enumerate(group_into_onsets(notes)):
        durations = [note_beats(note.note) * seconds_per_beat for note in group]
        onset = TimelineOnset(
            index=index,
            start_seconds=clock,
            advance_seconds=min(durations),
            notes=group,
            note_durations=durations,
        )
        timeline.append(onset)
        clock += onset.advance_seconds

    return timeline


def onset_at(timeline: list[TimelineOnset], seconds: float) -> TimelineOnset | None:
    """The onset sounding at `seconds` -- i.e. the last one to have started."""
    current = None
    for onset in timeline:
        if onset.start_seconds <= seconds:
            current = onset
        else:
            break
    return current
