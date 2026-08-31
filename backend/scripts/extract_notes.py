"""CLI: run OMR on one page of a PDF and dump the resulting note bounding
boxes as JSON.

For iterating on the OMR pipeline (oemer_engine.py) directly -- each run is
several minutes, and the upload UI reprocesses every page of a PDF on every
run, which is slow for testing one page's output repeatedly.

Usage:
    .venv/Scripts/python scripts/extract_notes.py <pdf_path> [--page N] [--out path.json]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import oemer_engine, omr  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf_path", type=Path)
    parser.add_argument("--page", type=int, default=0, help="0-indexed page number (default: 0)")
    parser.add_argument("--out", type=Path, default=None, help="Output JSON path (default: print to stdout)")
    args = parser.parse_args()

    pdf_bytes = args.pdf_path.read_bytes()
    rendered = omr.render_pages(pdf_bytes)

    matching = [p for p in rendered if p[0] == args.page]
    if not matching:
        raise SystemExit(f"PDF has {len(rendered)} page(s) (0-indexed); page {args.page} doesn't exist")
    index, png_bytes, width, height = matching[0]

    print(f"Running OMR on page {index} ({width}x{height})... this takes several minutes on CPU.", file=sys.stderr)
    boxes, music_xml = oemer_engine.extract_page(png_bytes, index, width, height)
    print(f"Done: {len(boxes)} notes detected.", file=sys.stderr)

    output = json.dumps([box.model_dump() for box in boxes], indent=2)
    if args.out:
        args.out.write_text(output)
        print(f"Wrote {args.out}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
