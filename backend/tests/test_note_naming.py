from app.services.note_naming import midi_to_note_name, step_alter_octave_to_note_name


def test_midi_to_note_name_middle_c():
    assert midi_to_note_name(60) == "C4"


def test_midi_to_note_name_a440():
    assert midi_to_note_name(69) == "A4"


def test_step_alter_octave_natural():
    assert step_alter_octave_to_note_name("E", 0, 4) == "E4"


def test_step_alter_octave_sharp():
    assert step_alter_octave_to_note_name("C", 1, 4) == "C#4"


def test_step_alter_octave_flat_matches_enharmonic_sharp_spelling():
    # Bb4 and A#4 are the same pitch -- and the same note the audio pitch
    # detector would report, so the two must produce an identical string.
    assert step_alter_octave_to_note_name("B", -1, 4) == "A#4"


def test_step_alter_octave_flat_crossing_octave_boundary():
    # Cb4 is enharmonically B3.
    assert step_alter_octave_to_note_name("C", -1, 4) == "B3"


def test_step_alter_octave_sharp_crossing_octave_boundary():
    # B#4 is enharmonically C5.
    assert step_alter_octave_to_note_name("B", 1, 4) == "C5"
