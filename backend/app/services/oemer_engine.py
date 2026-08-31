"""Glue code around the `oemer` OMR library.

oemer (https://github.com/BreezeWhite/oemer) is a real, pretrained-model-based
OMR pipeline: two U-Net passes segment staff lines/noteheads/symbols out of a
page image, then a rule-based pass groups them into measures, resolves each
notehead's pitch from its staff position + active clef/key/accidentals, and
emits MusicXML. It's genuinely slow on CPU (multiple minutes per page here,
no GPU available) and its public surface is a CLI that reads an image path
and writes a `.musicxml` file -- there's no in-memory/library API and no
notehead-bbox-to-pitch mapping exposed directly.

To get pitch *and* the pixel bounding box for each notehead (needed for the
frontend's note-highlighting overlay and line-following), this module
monkeypatches the three `Action` subclasses whose `.perform()` calls are what
actually resolve a notehead's pitch and emit its `<note>` XML element
(`AddNote`) and track the current measure number (`AddInit`/`AddMeasure`),
capturing `(notehead, measure_number, <note> element)` as oemer's own
pipeline produces them -- rather than re-deriving pitch ourselves (fragile
duplication of oemer's clef/key/accidental-state logic) or trying to
correlate the generated MusicXML back to noteheads by list order (fragile
across chords/rests). The patch is removed again immediately after.

oemer keeps its intermediate results (segmentation maps, detected noteheads,
etc.) in a process-global `layers` registry -- not safe for concurrent runs,
so `_LOCK` serializes all calls into this module. It also only accepts a
file path, not in-memory image bytes, so each call writes the page PNG to a
short-lived temp file (deleted immediately after, via TemporaryDirectory) --
a narrow, unavoidable exception to the app's normal zero-disk-writes rule for
uploaded content, scoped to one page's processing.
"""

import argparse
import os
import tempfile
import threading
import xml.etree.ElementTree as ET
from typing import Any

from app.models import NoteBoundingBox
from app.services.note_naming import step_alter_octave_to_note_name

_LOCK = threading.Lock()

# What's captured per notehead while oemer builds its MusicXML: the NoteHead
# object (for `.bbox`), the measure number active at that point, and the
# `<note>` element `AddNote.perform` produced for it (or None if oemer
# decided the note was invalid/out of representable range).
CapturedNote = tuple[Any, int, ET.Element | None]


def ensure_checkpoints() -> None:
    """Downloads oemer's pretrained model weights on first use (one-time,
    ~100s of MB, cached in the installed package directory). These are the
    app's own model assets, not user data, so caching them here doesn't
    conflict with the no-user-data-on-disk rule.
    """
    import oemer
    from oemer import ete

    chk_path = os.path.join(oemer.MODULE_PATH, "checkpoints/unet_big/model.onnx")
    if os.path.exists(chk_path):
        return

    for title, url in ete.CHECKPOINTS_URL.items():
        save_dir = "unet_big" if title.startswith("1st") else "seg_net"
        save_dir = os.path.join(oemer.MODULE_PATH, "checkpoints", save_dir)
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, title.split("_")[1])
        if not os.path.exists(save_path):
            ete.download_file(title, url, save_path)


def extract_page(png_bytes: bytes, page_index: int, page_width: int, page_height: int) -> tuple[list[NoteBoundingBox], str]:
    """Runs oemer end-to-end on one rendered page image. Returns note
    bounding boxes (rescaled from oemer's internal working resolution back
    to the page's actual pixel size) and that page's MusicXML.
    """
    import oemer.build_system as build_system_mod
    from oemer import ete, layers

    ensure_checkpoints()

    captured: list[tuple[object, int, object]] = []
    current_measure_number = 0

    original_add_note_perform = build_system_mod.AddNote.perform
    original_add_init_perform = build_system_mod.AddInit.perform
    original_add_measure_perform = build_system_mod.AddMeasure.perform

    def patched_add_init_perform(self, parent_elem=None):
        nonlocal current_measure_number
        elem = original_add_init_perform(self, parent_elem)
        current_measure_number = self.measure.number
        return elem

    def patched_add_measure_perform(self, parent_elem=None):
        nonlocal current_measure_number
        elem = original_add_measure_perform(self, parent_elem)
        current_measure_number = self.measure.number
        return elem

    def patched_add_note_perform(self, parent_elem=None):
        elem = original_add_note_perform(self, parent_elem)
        captured.append((self.note, current_measure_number, elem))
        return elem

    with _LOCK, tempfile.TemporaryDirectory() as tmp_dir:
        img_path = os.path.join(tmp_dir, "page.png")
        with open(img_path, "wb") as f:
            f.write(png_bytes)

        args = argparse.Namespace(
            img_path=img_path,
            output_path=tmp_dir,
            use_tf=False,
            save_cache=False,
            # PDF-rendered pages have no camera-style skew/warp to correct,
            # and skipping it keeps notehead bbox coordinates a plain scale
            # (not a nonlinear remap) away from the original page pixels.
            without_deskew=True,
        )

        ete.clear_data()
        build_system_mod.AddNote.perform = patched_add_note_perform
        build_system_mod.AddInit.perform = patched_add_init_perform
        build_system_mod.AddMeasure.perform = patched_add_measure_perform
        try:
            mxl_path = ete.extract(args)
            working_height, working_width = layers.get_layer("original_image").shape[:2]
            with open(mxl_path, "rb") as f:
                music_xml = f.read().decode("utf-8")
        finally:
            build_system_mod.AddNote.perform = original_add_note_perform
            build_system_mod.AddInit.perform = original_add_init_perform
            build_system_mod.AddMeasure.perform = original_add_measure_perform
            ete.clear_data()

    boxes = build_bounding_boxes(
        captured,
        page_index=page_index,
        scale_x=page_width / working_width,
        scale_y=page_height / working_height,
    )
    return boxes, music_xml


def build_bounding_boxes(
    captured: list[CapturedNote],
    page_index: int,
    scale_x: float,
    scale_y: float,
) -> list[NoteBoundingBox]:
    """Turns captured (notehead, measure_number, <note> element) triples into
    NoteBoundingBoxes, rescaling from oemer's working resolution to the
    page's actual pixel size. Split out from `extract_page` so this part --
    the actual data mapping -- is unit-testable without running oemer.
    """
    boxes: list[NoteBoundingBox] = []
    for note, measure_number, elem in captured:
        if elem is None or note.bbox is None:
            continue
        pitch_el = elem.find("pitch")
        if pitch_el is None:
            continue

        step = pitch_el.findtext("step")
        alter = int(pitch_el.findtext("alter") or "0")
        octave = int(pitch_el.findtext("octave"))
        note_type = elem.findtext("type") or "quarter"

        x1, y1, x2, y2 = note.bbox
        boxes.append(
            NoteBoundingBox(
                x=x1 * scale_x,
                y=y1 * scale_y,
                width=(x2 - x1) * scale_x,
                height=(y2 - y1) * scale_y,
                note=note_type,
                pitch=step_alter_octave_to_note_name(step, alter, octave),
                measureIndex=measure_number,
                pageIndex=page_index,
            )
        )

    return boxes
