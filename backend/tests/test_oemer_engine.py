import xml.etree.ElementTree as ET
from dataclasses import dataclass

from app.services.oemer_engine import build_bounding_boxes


@dataclass
class FakeNoteHead:
    bbox: tuple[int, int, int, int] | None


def _note_element(step: str, alter: int, octave: int, note_type: str = "quarter") -> ET.Element:
    elem = ET.Element("note")
    pitch = ET.SubElement(elem, "pitch")
    ET.SubElement(pitch, "step").text = step
    ET.SubElement(pitch, "alter").text = str(alter)
    ET.SubElement(pitch, "octave").text = str(octave)
    ET.SubElement(elem, "type").text = note_type
    return elem


def test_maps_captured_note_to_scaled_bounding_box():
    note = FakeNoteHead(bbox=(100, 200, 120, 220))
    captured = [(note, 3, _note_element("C", 0, 4, "half"))]

    boxes = build_bounding_boxes(captured, page_index=1, scale_x=2.0, scale_y=0.5)

    assert len(boxes) == 1
    box = boxes[0]
    assert box.x == 200
    assert box.y == 100
    assert box.width == 40  # (120 - 100) * 2.0
    assert box.height == 10  # (220 - 200) * 0.5
    assert box.pitch == "C4"
    assert box.note == "half"
    assert box.measureIndex == 3
    assert box.pageIndex == 1


def test_skips_notes_with_no_xml_element():
    # decode_note returns None for invalid/out-of-range notes.
    note = FakeNoteHead(bbox=(0, 0, 10, 10))
    captured = [(note, 1, None)]

    assert build_bounding_boxes(captured, page_index=0, scale_x=1.0, scale_y=1.0) == []


def test_skips_notes_with_no_bbox():
    note = FakeNoteHead(bbox=None)
    captured = [(note, 1, _note_element("D", 0, 5))]

    assert build_bounding_boxes(captured, page_index=0, scale_x=1.0, scale_y=1.0) == []


def test_skips_elements_with_no_pitch_defensively():
    # AddNote.perform only ever produces elements with a <pitch> (or None),
    # but guard against a future oemer version emitting something else.
    note = FakeNoteHead(bbox=(0, 0, 10, 10))
    elem = ET.Element("note")
    ET.SubElement(elem, "rest")
    captured = [(note, 1, elem)]

    assert build_bounding_boxes(captured, page_index=0, scale_x=1.0, scale_y=1.0) == []


def test_maps_multiple_notes_in_a_chord_independently():
    notes = [
        FakeNoteHead(bbox=(0, 0, 10, 10)),
        FakeNoteHead(bbox=(0, 20, 10, 30)),
    ]
    captured = [
        (notes[0], 1, _note_element("C", 0, 4)),
        (notes[1], 1, _note_element("E", 0, 4)),
    ]

    boxes = build_bounding_boxes(captured, page_index=0, scale_x=1.0, scale_y=1.0)

    assert [b.pitch for b in boxes] == ["C4", "E4"]
