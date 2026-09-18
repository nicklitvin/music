"""CLI: score note detection + position tracking on a REAL recording.

A real performance's true score position is not known up front, so it is
recovered by detecting note onsets in the recording and string-aligning
their detected pitch content to the score's onsets (one page's worth --
that is all OMR gives per run). The recording may contain later pages;
alignment stops at the last matched onset and only frames before then are
scored. Treat the alignment as a diagnostic aid, not exact -- "roughly
this bad", not to two decimals.

The evaluation logic itself lives in app/services/benchmark_eval.py, also
used by scripts/run_benchmarks.py to build the JSON `GET /api/benchmarks`
serves for local inspection.

Decode the recording first (it must be 16 kHz mono 16-bit WAV):
    ffmpeg -i recording.mp3 -ac 1 -ar 16000 -sample_fmt s16 recording.wav

Usage:
    .venv/Scripts/python scripts/evaluate_real_recording.py \
        <recording.wav> <notes.json> [--page 0] [--tempo 99]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import benchmark_eval as be  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("wav", type=Path)
    parser.add_argument("notes_json", type=Path, help="OMR note bounding boxes (extract_notes.py output)")
    parser.add_argument("--page", type=int, default=0)
    parser.add_argument("--tempo", type=float, default=99.0)
    args = parser.parse_args()

    notes = json.loads(args.notes_json.read_text(encoding="utf-8"))
    timeline = be.timeline_from_notes_json(notes, page=args.page, tempo_bpm=args.tempo)
    if not timeline:
        raise SystemExit(f"No notes for page {args.page} in {args.notes_json}")
    audio = be.load_wav(args.wav)

    print(f"{args.wav.name}: {len(audio) / be.RATE:.1f}s, page {args.page}: {len(timeline)} onsets")

    result = be.evaluate_piece(audio, timeline)
    if result is None:
        raise SystemExit("Could not align the recording to the score at all")

    print(f"alignment: matched {result.matchedOnsets}/{result.totalScoreOnsets} score onsets, "
          f"detected {result.detectedOnsets} onsets in the recording, "
          f"page material ends ~{result.lastMatchedTimeSeconds}s")
    for w in result.warnings:
        print(f"  WARNING: {w}")

    d = result.detection
    print("\nnote detection (HarmonicSalienceEstimator.top_pitches vs what is sounding)")
    print(f"  scored frames             {d['scoredFrames']}")
    print(f"  exact-pitch  P/R/F1       {d['exactPitch']['precision']:.2f} / {d['exactPitch']['recall']:.2f} / {d['exactPitch']['f1']:.2f}")
    print(f"  pitch-class  P/R/F1       {d['pitchClass']['precision']:.2f} / {d['pitchClass']['recall']:.2f} / {d['pitchClass']['f1']:.2f}")
    if d["octaveErrorShareOfFalsePositives"] is not None:
        print(f"  octave errors / all FPs   {100 * d['octaveErrorShareOfFalsePositives']:.0f}%")
    if d["highestNoteExact"] is not None:
        print(f"  highest note exact        {100 * d['highestNoteExact']:.0f}%")
        print(f"  highest note pitch-class  {100 * d['highestNotePitchClass']:.0f}%")

    print("\nposition tracking (reported onset vs onset-alignment ground truth)")
    print(f"  {'method':<18}{'exact':>8}{'+/-1':>8}{'+/-2':>8}{'+/-3':>8}{'MAE':>9}")
    for name, m in result.tracking.items():
        if m is None:
            print(f"  {name:<18}{'no frames':>8}")
            continue
        print(f"  {name:<18}{m['exact']:>7.1f}%{m['within1']:>7.1f}%{m['within2']:>7.1f}%{m['within3']:>7.1f}%{m['meanAbsoluteError']:>9.2f}")

    lp = result.livePath["playthrough"]
    print("\nlive path (seed@0 + rate limiter) -- full playthrough")
    print(f"  final onset {lp['finalOnset']}/{lp['totalOnsets'] - 1}   "
          f"largest single-frame move {lp['largestSingleFrameMove']}   moves >3 onsets: {lp['movesOverThreeOnsets']}")
    if lp["meanAbsoluteError"] is not None:
        print(f"  MAE {lp['meanAbsoluteError']}   within +/-3: {lp['within3']}%   max error {lp['maxError']}")

    sp = result.livePath["startPoints"]
    print(f"\nlive path -- cold start from anywhere (no hint), {sp['total']} points across the matched recording")
    print(f"  {'from':>8}  {'locks after':>12}")
    for r in sp["results"]:
        lock = "never" if r["locksAfterSeconds"] is None else f"{r['locksAfterSeconds']}s"
        print(f"  {r['startSeconds']:>7}s  {lock:>12}")
    if sp["withinTargetPct"] is not None:
        print(f"  locked {sp['lockedCount']}/{sp['total']}, within {sp['targetSeconds']}s: "
              f"{sp['withinTarget']}/{sp['total']} ({sp['withinTargetPct']}%)")


if __name__ == "__main__":
    main()
