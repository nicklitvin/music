"""One-off batch: run OMR page-0 extraction for every new piece added to
content/full/ that doesn't have a notes.json yet. Logs progress with
timestamps since each page is several minutes on CPU.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import oemer_engine, omr  # noqa: E402

CONTENT = Path(__file__).resolve().parents[2] / "content" / "full"

PIECES = [
    "angel-thesis",
    "melissa",
    # "guren" is running separately (already kicked off before this batch existed).
    "last-stardust",
    "departure",
    "sugar-song",
    "unravel",
]


def main() -> None:
    for slug in PIECES:
        piece_dir = CONTENT / slug
        pdf_path = piece_dir / "score.pdf"
        out_path = piece_dir / "notes.json"
        if out_path.exists():
            print(f"[{time.strftime('%H:%M:%S')}] {slug}: notes.json already exists, skipping", flush=True)
            continue
        if not pdf_path.exists():
            print(f"[{time.strftime('%H:%M:%S')}] {slug}: no score.pdf, skipping", flush=True)
            continue

        print(f"[{time.strftime('%H:%M:%S')}] {slug}: rendering + running OMR on page 0...", flush=True)
        start = time.monotonic()
        rendered = omr.render_pages(pdf_path.read_bytes())
        index, png_bytes, width, height = rendered[0]
        boxes, _music_xml = oemer_engine.extract_page(png_bytes, index, width, height)
        elapsed = time.monotonic() - start

        out_path.write_text(
            json.dumps([box.model_dump() for box in boxes], indent=2),
            encoding="utf-8",
        )
        print(f"[{time.strftime('%H:%M:%S')}] {slug}: done in {elapsed:.0f}s, {len(boxes)} notes -> {out_path}", flush=True)


if __name__ == "__main__":
    main()
