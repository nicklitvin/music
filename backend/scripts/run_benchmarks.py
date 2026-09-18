"""CLI: run the whole benchmark suite and write the JSON the frontend's
Benchmarks page reads.

Two kinds of test, matching content/'s layout:

  * "full" -- one page of a real sheet (OMR'd via extract_notes.py) scored
    against its real accompanying recording. Ground truth is recovered by
    aligning detected audio onsets to the score (see benchmark_eval.py);
    it is a diagnostic aid, not exact.
        content/full/<piece>/score.pdf
        content/full/<piece>/notes.json        (extract_notes.py output)
        content/full/<piece>/performance.*     (mp3/mp4/wav/... optional)

  * "sample" -- a synthesized WAV (synthesize_score.py) whose true position
    is known exactly at every instant.
        content/samples/<piece>/<variant>.wav
        content/samples/<piece>/<variant>-truth.json

content/ is gitignored (real, often copyrighted, sheet music and
recordings), so this script -- and its inputs -- never get committed. Only
the aggregate metrics in the output JSON do.

Usage:
    .venv/Scripts/python scripts/run_benchmarks.py [--only slug,slug] [--skip-samples]
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
SAMPLES_DIR = CONTENT / "samples"
RESULTS_PATH = Path(__file__).resolve().parents[1] / "benchmark_results.json"

# A handful of flawed-performance presets (scripts/performance_simulation.py
# has more) generated for any piece that doesn't already have samples of
# its own -- aliez keeps its full, hand-curated set of every preset.
DEFAULT_SAMPLE_PRESETS = ["clean", "realistic", "sloppy", "noisy"]


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


def ensure_default_samples(slug: str, notes_json: Path, out_dir: Path) -> None:
    """Synthesizes a few flawed-performance samples for a piece that has
    none yet, by shelling out to synthesize_score.py (which already does
    this correctly -- no reason to duplicate its synthesis logic)."""
    if out_dir.exists() and any(out_dir.glob("*.wav")):
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    for preset in DEFAULT_SAMPLE_PRESETS:
        stem = "clean" if preset == "clean" else preset
        out_wav = out_dir / f"{stem}.wav"
        out_truth = out_dir / f"{stem}-truth.json"
        log(f"  synthesizing {slug}/{stem}.wav ...")
        subprocess.run(
            [
                sys.executable, str(REPO_ROOT / "backend" / "scripts" / "synthesize_score.py"),
                str(notes_json), "--page", "0", "--tempo", "99",
                "--performance", preset, "--out-wav", str(out_wav), "--out-truth", str(out_truth),
            ],
            check=True, capture_output=True, cwd=REPO_ROOT / "backend",
        )


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

    log(f"{slug}: done -- {result.matchedOnsets}/{result.totalScoreOnsets} onsets matched, "
        f"live MAE {result.livePath['playthrough']['meanAbsoluteError']}")
    return {"slug": slug, "hasPerformance": True, **result.__dict__}


def run_samples_for_piece(slug: str, sample_dir: Path) -> list[dict]:
    results = []
    for truth_path in sorted(sample_dir.glob("*-truth.json")):
        variant = truth_path.name.removesuffix("-truth.json")
        wav_path = sample_dir / f"{variant}.wav"
        if not wav_path.exists():
            continue
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        timeline = be.timeline_from_truth_json(truth)
        audio = be.load_wav(wav_path)
        true_starts = [o["startSeconds"] for o in truth["onsets"]]
        log(f"  {slug}/{variant}: evaluating ({len(audio) / be.RATE:.0f}s)...")
        results.append({"variant": variant, **be.evaluate_sample(audio, timeline, true_starts)})
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", type=str, default=None, help="Comma-separated slugs to limit to")
    parser.add_argument("--skip-samples", action="store_true")
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

    sample_results = []
    if not args.skip_samples:
        for piece_dir in sorted(FULL_DIR.iterdir()):
            if not piece_dir.is_dir():
                continue
            slug = piece_dir.name
            if only and slug not in only:
                continue
            notes_json = piece_dir / "notes.json"
            sample_dir = SAMPLES_DIR / slug
            if not notes_json.exists():
                continue
            if slug != "aliez":  # aliez keeps its own hand-picked full preset set
                ensure_default_samples(slug, notes_json, sample_dir)
            if sample_dir.exists():
                variants = run_samples_for_piece(slug, sample_dir)
                if variants:
                    sample_results.append({"slug": slug, "variants": variants})

    RESULTS_PATH.write_text(
        json.dumps(
            {
                "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "full": full_results,
                "samples": sample_results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    log(f"wrote {RESULTS_PATH}")


if __name__ == "__main__":
    main()
