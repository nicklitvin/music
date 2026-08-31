"""CLI: overlay a notes JSON (from extract_notes.py) onto its source PDF
page, so the detected notes/pitches can be visually fact-checked against
the real sheet music.

Usage:
    .venv/Scripts/python scripts/annotate_notes.py <pdf_path> <notes_json> [--page N] [--out path.png]
"""

import argparse
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from app.services import omr  # noqa: E402

BOX_COLOR = (220, 30, 30)
LABEL_COLOR = (220, 30, 30)
LABEL_BG = (255, 255, 255)


def _load_font(size: int) -> ImageFont.ImageFont:
    for candidate in (r"C:\Windows\Fonts\consola.ttf", r"C:\Windows\Fonts\arial.ttf"):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf_path", type=Path)
    parser.add_argument("notes_json", type=Path)
    parser.add_argument("--page", type=int, default=0, help="0-indexed page number (default: 0)")
    parser.add_argument("--out", type=Path, default=None, help="Output PNG path (default: <pdf stem>-annotated.png)")
    parser.add_argument("--max-width", type=int, default=2200, help="Downscale output to this width (default: 2200)")
    args = parser.parse_args()

    out_path = args.out or args.pdf_path.with_name(f"{args.pdf_path.stem}-page{args.page}-annotated.png")

    notes = json.loads(args.notes_json.read_text())
    notes_on_page = [n for n in notes if n["pageIndex"] == args.page]
    print(f"{len(notes_on_page)} of {len(notes)} notes in the JSON are on page {args.page}", file=sys.stderr)

    pdf_bytes = args.pdf_path.read_bytes()
    rendered = omr.render_pages(pdf_bytes)
    matching = [p for p in rendered if p[0] == args.page]
    if not matching:
        raise SystemExit(f"PDF has {len(rendered)} page(s) (0-indexed); page {args.page} doesn't exist")
    _, png_bytes, width, height = matching[0]

    image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    draw = ImageDraw.Draw(image)
    font_size = max(14, round(width / 110))
    font = _load_font(font_size)
    line_width = max(2, round(width / 1200))

    for note in notes_on_page:
        x, y, w, h = note["x"], note["y"], note["width"], note["height"]
        draw.rectangle([x, y, x + w, y + h], outline=BOX_COLOR, width=line_width)

        label = note["pitch"]
        text_x, text_y = x, y - font_size - 2
        bbox = draw.textbbox((text_x, text_y), label, font=font)
        draw.rectangle(bbox, fill=LABEL_BG)
        draw.text((text_x, text_y), label, fill=LABEL_COLOR, font=font)

    if args.max_width and image.width > args.max_width:
        scale = args.max_width / image.width
        image = image.resize((args.max_width, round(image.height * scale)), Image.LANCZOS)

    image.save(out_path)
    print(f"Wrote {out_path} ({image.width}x{image.height})", file=sys.stderr)


if __name__ == "__main__":
    main()
