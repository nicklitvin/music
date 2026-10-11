"""CLI: time OMR on page 0 of a test piece under different oemer settings,
and check the notes against that piece's stored notes.json.

For deciding what makes oemer cheaper without making it worse. Prints the
wall time and CPU time per oemer stage, and how many of the stored notes
come back (same pitch, centre within 12 px at 300 dpi). The stored notes
are oemer's own output at its defaults, not ground truth: where the two
disagree, look at the score to see which is right.

Usage:
    .venv/Scripts/python scripts/omr_cost_probe.py <piece> [--dpi N]
        [--tile N] [--overlap N] [--threads N] [--pixels N] [--oemer-tiling]

    --tile / --overlap   the symbol net's tiles (OMR_TILE_SIZE/_OVERLAP)
    --threads            threads for the nets; 2 is close to the server
    --pixels             oemer's working size; by default it rescales
                         every page to ~3.7M pixels
    --oemer-tiling       oemer's own inference, for comparison
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402

CONTENT = Path(__file__).resolve().parents[2] / "content" / "full"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("piece")
    parser.add_argument("--dpi", type=int, default=settings.render_dpi)
    parser.add_argument("--tile", type=int, default=settings.omr_tile_size)
    parser.add_argument("--overlap", type=int, default=settings.omr_tile_overlap)
    parser.add_argument("--threads", type=int, default=settings.omr_threads)
    parser.add_argument("--pixels", type=float, default=None)
    parser.add_argument("--oemer-tiling", action="store_true")
    args = parser.parse_args()

    settings.render_dpi = args.dpi
    settings.omr_tile_size = args.tile
    settings.omr_tile_overlap = args.overlap
    settings.omr_threads = args.threads

    import oemer.inference

    from app.services import oemer_engine, oemer_inference, omr

    if args.oemer_tiling:
        oemer_inference.inference = oemer.inference.inference

    if args.pixels:

        def resize_image(image):
            w, h = image.size
            ratio = (args.pixels / (w * h)) ** 0.5
            return image.resize((round(w * ratio), round(h * ratio)))

        oemer.inference.resize_image = resize_image

    stages: list[tuple[str, float, float]] = []

    class StageTimer(logging.Handler):
        def emit(self, record):
            stages.append((record.getMessage(), time.perf_counter(), time.process_time()))

    oemer_logger = logging.getLogger("oemer")
    oemer_logger.addHandler(StageTimer())
    oemer_logger.setLevel(logging.INFO)

    pdf = CONTENT / args.piece / "score.pdf"
    index, png, width, height = omr.render_pages(pdf.read_bytes())[0]
    start, start_cpu = time.perf_counter(), time.process_time()
    boxes, _ = oemer_engine.extract_page(png, index, width, height)
    end, end_cpu = time.perf_counter(), time.process_time()

    marks = stages + [("end", end, end_cpu)]
    per_stage = {m: [round(tn - t), round(cn - c)] for (m, t, c), (_, tn, cn) in zip(marks, marks[1:])}

    stored = [n for n in json.loads((pdf.parent / "notes.json").read_text()) if n["pageIndex"] == 0]
    scale = 300 / args.dpi
    found = [(b.pitch, (b.x + b.width / 2) * scale, (b.y + b.height / 2) * scale) for b in boxes]
    used: set[int] = set()
    for note in stored:
        cx, cy = note["x"] + note["width"] / 2, note["y"] + note["height"] / 2
        for i, (pitch, x, y) in enumerate(found):
            if i not in used and pitch == note["pitch"] and abs(x - cx) < 12 and abs(y - cy) < 12:
                used.add(i)
                break

    print(
        json.dumps(
            {
                "piece": args.piece,
                "wall_seconds": round(end - start),
                "cpu_seconds": round(end_cpu - start_cpu),
                "notes": len(boxes),
                "stored_notes": len(stored),
                "matching": len(used),
                "stages_wall_cpu": per_stage,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
