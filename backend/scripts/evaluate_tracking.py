"""CLI: score every position-tracking method against synthesized audio.

Feeds the WAV from synthesize_score.py through each tracker in
app/services/position_tracking.py frame by frame and compares the position
it reports against the ground-truth timeline the audio was generated from.

Accuracy is the share of non-silent frames whose reported onset index is
correct. `exact` requires the precise onset; `+/-1` and `+/-2` allow the
tracker to lag or lead by an onset or two, which still puts a performer on
the right spot on the page.

Usage:
    .venv/Scripts/python scripts/evaluate_tracking.py <wav> <truth_json>
        [--method NAME] [--frame-ms 75]
"""

import argparse
import json
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from app.services.position_tracking import (  # noqa: E402
    METHODS,
    SILENCE_RMS_THRESHOLD,
    TrackerConfig,
    build_tracker,
)
from app.services.score_timeline import TimelineOnset  # noqa: E402
from app.models import NoteBoundingBox  # noqa: E402


def load_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
            raise SystemExit("Expected 16-bit mono WAV")
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float64), rate


def load_timeline(truth: dict) -> list[TimelineOnset]:
    # Only pitches and ordering matter to the trackers; the bounding boxes
    # are rebuilt as placeholders so TimelineOnset stays the single shape
    # both the live pipeline and this harness pass around.
    timeline = []
    for entry in truth["onsets"]:
        notes = [
            NoteBoundingBox(
                x=entry["x"],
                y=entry["y"],
                width=1,
                height=1,
                note="quarter",
                pitch=pitch,
                measureIndex=entry["measureIndex"],
                pageIndex=truth["pageIndex"],
            )
            for pitch in entry["pitches"]
        ]
        timeline.append(
            TimelineOnset(
                index=entry["index"],
                start_seconds=entry["startSeconds"],
                advance_seconds=entry["advanceSeconds"],
                notes=notes,
                # Real per-note lengths, not advanceSeconds -- held notes
                # outlast the gap to the next onset, and the trackers need
                # that to know what is still ringing.
                note_durations=entry["durations"],
            )
        )
    return timeline


def true_index_at(starts: np.ndarray, seconds: float) -> int:
    return int(np.searchsorted(starts, seconds, side="right") - 1)


def evaluate(method: str, samples: np.ndarray, rate: int, timeline: list[TimelineOnset], frame_samples: int) -> dict:
    tracker = build_tracker(method, timeline, TrackerConfig())
    starts = np.array([onset.start_seconds for onset in timeline])

    exact = near1 = near2 = considered = 0
    errors: list[int] = []

    for start in range(0, len(samples) - frame_samples, frame_samples):
        frame = samples[start : start + frame_samples]
        centre_seconds = (start + frame_samples / 2) / rate

        if float(np.sqrt(np.mean(np.square(frame)))) < SILENCE_RMS_THRESHOLD:
            tracker.observe(frame)
            continue

        truth_index = true_index_at(starts, centre_seconds)
        if truth_index < 0:
            continue

        predicted = tracker.observe(frame)
        error = predicted - truth_index
        errors.append(error)
        considered += 1
        exact += error == 0
        near1 += abs(error) <= 1
        near2 += abs(error) <= 2

    if considered == 0:
        return {"method": method, "frames": 0}

    array = np.array(errors)
    return {
        "method": method,
        "frames": considered,
        "exact": 100 * exact / considered,
        "within1": 100 * near1 / considered,
        "within2": 100 * near2 / considered,
        "median_error": float(np.median(array)),
        "mean_abs_error": float(np.mean(np.abs(array))),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("wav", type=Path)
    parser.add_argument("truth_json", type=Path)
    parser.add_argument("--method", default=None, help="Evaluate one method (default: all)")
    parser.add_argument("--frame-ms", type=float, default=75.0, help="Analysis frame size, matching the live pipeline")
    args = parser.parse_args()

    samples, rate = load_wav(args.wav)
    truth = json.loads(args.truth_json.read_text(encoding="utf-8"))
    timeline = load_timeline(truth)
    frame_samples = int(rate * args.frame_ms / 1000)

    print(f"{args.wav.name}: {len(samples)/rate:.1f}s, {len(timeline)} onsets, {args.frame_ms:.0f}ms frames\n")
    header = f"{'method':<20} {'frames':>7} {'exact':>8} {'+/-1':>8} {'+/-2':>8} {'medErr':>8} {'MAE':>8}"
    print(header)
    print("-" * len(header))

    for method in [args.method] if args.method else METHODS:
        result = evaluate(method, samples, rate, timeline, frame_samples)
        if not result.get("frames"):
            print(f"{method:<20} {'no frames':>7}")
            continue
        print(
            f"{result['method']:<20} {result['frames']:>7} "
            f"{result['exact']:>7.1f}% {result['within1']:>7.1f}% {result['within2']:>7.1f}% "
            f"{result['median_error']:>8.1f} {result['mean_abs_error']:>8.2f}"
        )


if __name__ == "__main__":
    main()
