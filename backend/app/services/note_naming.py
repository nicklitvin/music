"""Shared note-name <-> MIDI helpers.

Both the audio pitch detector and the OMR pipeline need to name notes the
same way ("C4", "C#4", ...) so the frontend can match a detected note
against a score's note bounding boxes with plain string equality.
"""

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Semitone offset from C for each natural letter name (before applying any
# sharp/flat alteration).
_NATURAL_SEMITONES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def midi_to_note_name(midi: int) -> str:
    name = NOTE_NAMES[midi % 12]
    octave = midi // 12 - 1
    return f"{name}{octave}"


def step_alter_octave_to_note_name(step: str, alter: int, octave: int) -> str:
    """Convert MusicXML-style (step, alter, octave) -- e.g. ("B", -1, 4) for
    Bb4 -- into the same "C"/"C#"/... spelling `midi_to_note_name` produces,
    so flats and sharps that are enharmonically equal come out identical.
    """
    midi = (octave + 1) * 12 + _NATURAL_SEMITONES[step] + alter
    return midi_to_note_name(midi)
