"""CLI: run the whole benchmark suite and write the JSON `GET
/api/benchmarks` serves for local inspection (see backend/README for why
nothing in the UI shows it).

Every piece is real: one page of a real sheet (OMR'd via extract_notes.py)
scored against its real accompanying recording. Ground truth is recovered
by aligning detected audio onsets to the score (see benchmark_eval.py); it
is a diagnostic aid, not exact.

    content/full/<piece>/score.pdf
    content/full/<piece>/notes.json        (extract_notes.py output)
    content/full/<piece>/performance.*     (mp3/mp4/wav/... optional)

An earlier version of this also scored synthesized WAVs
(content/samples/<piece>/) with exactly-known ground truth. That machinery
-- and the samples themselves -- was retired: the numbers that actually
matter are how well tracking does on real recordings, tried from many
start points (see `evaluate_start_points` in benchmark_eval.py), not on
audio rendered from the score itself. The retired samples are kept out of
the pipeline under content/archive/samples/ in case they're useful again.

content/ is gitignored (real, often copyrighted, sheet music and
recordings), so this script -- and its inputs -- never get committed. Only
the aggregate metrics in the output JSON do.

Usage:
    .venv/Scripts/python scripts/run_benchmarks.py [--only slug,slug]
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import imageio_ffmpeg  # noqa: E402

from app.services import benchmark_eval as be  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTENT = REPO_ROOT / "content"
FULL_DIR = CONTENT / "full"
RESULTS_PATH = Path(__file__).resolve().parents[1] / "benchmark_results.json"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def decode_to_wav(src: Path, out: Path) -> None:
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run(
        [exe, "-y", "-i", str(src), "-ac", "1", "-ar", str(be.RATE), "-sample_fmt", "s16", str(out)],
        check=True, capture_output=True,
    )


def find_performance_file(piece_dir: Path) -> Path | None:
    candidates = [p for p in sorted(piece_dir.glob("performance.*")) if p.suffix.lower() not in (".json", ".wav")]
    return candidates[0] if candidates else None


def run_full_piece(slug: str, piece_dir: Path) -> dict | None:
    performance = find_performance_file(piece_dir)
    if performance is None:
        # No recording to test against regardless of OMR status -- list it
        # so the reorg's coverage is visible, but don't spend minutes of
        # OMR on a sheet there's no way to score.
        log(f"{slug}: no performance recording, skipping accuracy test (sheet only)")
        return {"slug": slug, "hasPerformance": False}

    notes_json = piece_dir / "notes.json"
    if not notes_json.exists():
        log(f"{slug}: has a performance recording but no notes.json yet, skipping")
        return {"slug": slug, "hasPerformance": True, "error": "OMR not yet run on this sheet"}

    wav_path = piece_dir / "_performance_16k.wav"
    log(f"{slug}: decoding {performance.name} ...")
    decode_to_wav(performance, wav_path)

    notes = json.loads(notes_json.read_text(encoding="utf-8"))
    timeline = be.timeline_from_notes_json(notes, page=0, tempo_bpm=99.0)
    if not timeline:
        log(f"{slug}: notes.json has no page-0 notes, skipping")
        return {"slug": slug, "hasPerformance": True, "error": "no notes extracted"}

    audio = be.load_wav(wav_path)
    log(f"{slug}: evaluating ({len(audio) / be.RATE:.0f}s recording, {len(timeline)} onsets)...")
    result = be.evaluate_piece(audio, timeline)
    if result is None:
        log(f"{slug}: could not align recording to score")
        return {"slug": slug, "hasPerformance": True, "error": "alignment failed"}

    sp = result.livePath["startPoints"]
    log(f"{slug}: done -- {result.matchedOnsets}/{result.totalScoreOnsets} onsets matched, "
        f"start-anywhere within {sp['targetSeconds']}s: {sp['withinTarget']}/{sp['total']}")
    return {"slug": slug, "hasPerformance": True, **result.__dict__}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", type=str, default=None, help="Comma-separated slugs to limit to")
    args = parser.parse_args()
    only = set(args.only.split(",")) if args.only else None

    full_results = []
    for piece_dir in sorted(FULL_DIR.iterdir()):
        if not piece_dir.is_dir():
            continue
        slug = piece_dir.name
        if only and slug not in only:
            continue
        result = run_full_piece(slug, piece_dir)
        if result:
            full_results.append(result)

    RESULTS_PATH.write_text(
        json.dumps({"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"), "full": full_results}, indent=2),
        encoding="utf-8",
    )
    log(f"wrote {RESULTS_PATH}")


if __name__ == "__main__":
    main()
