"""CLI: OMR every page of every piece that has both a sheet and a real
recording, accumulating into content/full/<piece>/notes.json.

Two things make this survivable as a multi-hour job:

* **Resumable.** notes.json is rewritten after each page completes, and
  pages already present in it are skipped, so a kill (or an OOM -- oemer
  is memory-hungry) only loses the page in flight.
* **One page per subprocess.** oemer holds a lot of memory per run; a
  fresh process per page hands it all back to the OS in between, which a
  single long-lived process does not.

Pages are visited round-robin across pieces (page 1 of everything, then
page 2 of everything, ...) rather than piece by piece, so every recording
has its opening pages available early -- that's the material the start-time
tracking experiments need first.

Usage:
    .venv/Scripts/python scripts/extract_all_pages.py [--only slug,slug]
        [--max-page N]
"""

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import omr  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
CONTENT_FULL = BACKEND.parent / "content" / "full"
EXTRACT_ONE = BACKEND / "scripts" / "extract_notes.py"


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def has_recording(piece_dir: Path) -> bool:
    return any(p for p in piece_dir.glob("performance.*") if p.suffix.lower() not in (".json", ".wav"))


def load_notes(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def extract_one_page(pdf: Path, page: int) -> list[dict]:
    """Runs OMR for a single page in a fresh subprocess, so its memory is
    fully released before the next page starts."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "page.json"
        subprocess.run(
            [sys.executable, str(EXTRACT_ONE), str(pdf), "--page", str(page), "--out", str(out)],
            check=True,
            cwd=BACKEND,
            capture_output=True,
        )
        return json.loads(out.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", type=str, default=None, help="Comma-separated slugs to limit to")
    parser.add_argument("--max-page", type=int, default=None, help="Stop after this page index (inclusive)")
    args = parser.parse_args()
    only = set(args.only.split(",")) if args.only else None

    # Plan: (slug, notes_path, pdf, page) for every page still missing.
    plans: dict[str, dict] = {}
    for piece_dir in sorted(CONTENT_FULL.iterdir()):
        if not piece_dir.is_dir():
            continue
        slug = piece_dir.name
        if only and slug not in only:
            continue
        pdf = piece_dir / "score.pdf"
        if not pdf.exists() or not has_recording(piece_dir):
            continue
        notes_path = piece_dir / "notes.json"
        page_count = len(omr.render_pages(pdf.read_bytes()))
        have = {note["pageIndex"] for note in load_notes(notes_path)}
        missing = [p for p in range(page_count) if p not in have]
        if args.max_page is not None:
            missing = [p for p in missing if p <= args.max_page]
        if missing:
            plans[slug] = {"pdf": pdf, "notes": notes_path, "missing": missing}

    total = sum(len(plan["missing"]) for plan in plans.values())
    log(f"{total} page(s) to extract across {len(plans)} piece(s)")

    # Round-robin by page position so every piece gets its early pages first.
    done = 0
    while plans:
        for slug in list(plans):
            plan = plans[slug]
            page = plan["missing"].pop(0)
            log(f"{slug} page {page}: running OMR ...")
            started = time.monotonic()
            try:
                boxes = extract_one_page(plan["pdf"], page)
            except subprocess.CalledProcessError as err:
                stderr = (err.stderr or b"").decode("utf-8", "replace")[-400:]
                log(f"{slug} page {page}: FAILED -- {stderr}")
                boxes = None
            if boxes is not None:
                notes = load_notes(plan["notes"])
                notes = [note for note in notes if note["pageIndex"] != page] + boxes
                notes.sort(key=lambda note: (note["pageIndex"], note["measureIndex"], note["x"], note["y"]))
                plan["notes"].write_text(json.dumps(notes, indent=2), encoding="utf-8")
                done += 1
                log(
                    f"{slug} page {page}: {len(boxes)} notes in {time.monotonic() - started:.0f}s "
                    f"({done}/{total} pages done)"
                )
            if not plan["missing"]:
                del plans[slug]

    log("all pages extracted")


if __name__ == "__main__":
    main()
