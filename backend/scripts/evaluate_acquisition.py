"""CLI: measure how fast score position is found from a cold start.

The tracker is given no hint about where playback began -- it starts from
a uniform belief over the whole score. Playback is then started from many
different points in the reference WAV, and for each one we measure how long
until the reported position is correct *and stays* correct.

"Correct" allows a small onset tolerance because neighbouring onsets share
most of their sounding notes, so being one onset out still puts a reader on
the right spot on the page. Requiring the estimate to hold, rather than
just to be briefly right, is what separates a real lock from a lucky frame.

Usage:
    .venv/Scripts/python scripts/evaluate_acquisition.py <wav> <truth_json>
        [--starts 20] [--target-seconds 3.0] [--tolerance 2]
"""

import argparse
import json
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from app.services.note_estimation import is_silent  # noqa: E402
from app.services.position_markov import MarkovConfig, MarkovPositionTracker  # noqa: E402
from app.models import NoteBoundingBox  # noqa: E402
from app.services.score_timeline import TimelineOnset  # noqa: E402


def load_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
            raise SystemExit("Expected 16-bit mono WAV")
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float64), rate


def load_timeline(truth: dict) -> list[TimelineOnset]:
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
                note_durations=entry["durations"],
            )
        )
    return timeline


def acquisition_time(
    samples: np.ndarray,
    rate: int,
    timeline: list[TimelineOnset],
    starts: np.ndarray,
    begin_seconds: float,
    frame_samples: int,
    tolerance: int,
    hold_seconds: float,
) -> tuple[float | None, float]:
    """Seconds from `begin_seconds` until the estimate locks on, plus the
    confidence at that moment. None if it never locks and holds.
    """
    tracker = MarkovPositionTracker(timeline, MarkovConfig())
    first_sample = int(begin_seconds * rate)
    hold_frames = max(1, int(hold_seconds * rate / frame_samples))

    history: list[tuple[float, bool, float]] = []
    for start in range(first_sample, len(samples) - frame_samples, frame_samples):
        frame = samples[start : start + frame_samples]
        centre = (start + frame_samples / 2) / rate
        estimate = tracker.observe(frame)

        if is_silent(frame):
            continue
        truth_index = int(np.searchsorted(starts, centre, side="right") - 1)
        if truth_index < 0:
            continue

        history.append((centre - begin_seconds, abs(estimate.index - truth_index) <= tolerance, estimate.confidence))

    # A lock is the first correct estimate that then stays correct for at
    # least `hold_seconds`; a single correct frame is not a lock.
    run = 0
    lock_elapsed: float | None = None
    confidence_at_lock = 0.0
    for elapsed, correct, confidence in history:
        if not correct:
            run = 0
            lock_elapsed = None
            continue
        if run == 0:
            lock_elapsed, confidence_at_lock = elapsed, confidence
        run += 1
        if run >= hold_frames:
            return lock_elapsed, confidence_at_lock
    return None, 0.0


def confident_estimate(
    samples: np.ndarray,
    rate: int,
    timeline: list[TimelineOnset],
    starts: np.ndarray,
    begin_seconds: float,
    frame_samples: int,
    tolerance: int,
    min_confidence: float,
) -> tuple[float | None, bool]:
    """How this is actually meant to be used: wait until the model says it
    is confident, then trust it. Returns when confidence first crosses the
    threshold and whether the estimate was right at that moment.
    """
    tracker = MarkovPositionTracker(timeline, MarkovConfig())

    for start in range(int(begin_seconds * rate), len(samples) - frame_samples, frame_samples):
        frame = samples[start : start + frame_samples]
        centre = (start + frame_samples / 2) / rate
        estimate = tracker.observe(frame)

        if is_silent(frame):
            continue
        truth_index = int(np.searchsorted(starts, centre, side="right") - 1)
        if truth_index < 0:
            continue

        if estimate.confidence >= min_confidence:
            return centre - begin_seconds, abs(estimate.index - truth_index) <= tolerance
    return None, False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("wav", type=Path)
    parser.add_argument("truth_json", type=Path)
    parser.add_argument("--starts", type=int, default=20, help="How many start points to try")
    parser.add_argument("--target-seconds", type=float, default=3.0)
    parser.add_argument("--tolerance", type=int, default=2, help="Onsets of slack allowed")
    parser.add_argument("--hold-seconds", type=float, default=2.0, help="How long the estimate must hold to count")
    parser.add_argument("--min-confidence", type=float, default=0.95, help="Threshold for the wait-until-confident test")
    parser.add_argument("--frame-ms", type=float, default=75.0)
    args = parser.parse_args()

    samples, rate = load_wav(args.wav)
    truth = json.loads(args.truth_json.read_text(encoding="utf-8"))
    timeline = load_timeline(truth)
    starts = np.array([onset.start_seconds for onset in timeline])
    frame_samples = int(rate * args.frame_ms / 1000)

    duration = len(samples) / rate
    # Leave room after each start point for the tracker to actually lock.
    begin_points = np.linspace(0, max(duration - 12.0, 0.1), args.starts)

    print(f"{args.wav.name}: {duration:.1f}s, {len(timeline)} onsets")
    print(f"cold start from {args.starts} points | tolerance +/-{args.tolerance} onsets | "
          f"must hold {args.hold_seconds}s | target {args.target_seconds}s\n")
    header = f"{'start(s)':>9} {'trueOnset':>10} {'lock(s)':>9} {'conf':>7} {'conf>=thr(s)':>13} {'ok':>4}"
    print(header)
    print("-" * len(header))

    times: list[float] = []
    confident_times: list[float] = []
    confident_correct = 0
    for begin in begin_points:
        lock, confidence = acquisition_time(
            samples, rate, timeline, starts, float(begin), frame_samples, args.tolerance, args.hold_seconds
        )
        conf_time, conf_ok = confident_estimate(
            samples, rate, timeline, starts, float(begin), frame_samples, args.tolerance, args.min_confidence
        )
        true_onset = int(np.searchsorted(starts, begin, side="right") - 1)

        lock_text = "never" if lock is None else f"{lock:.2f}"
        conf_text = "never" if conf_time is None else f"{conf_time:.2f}"
        if lock is not None:
            times.append(lock)
        if conf_time is not None:
            confident_times.append(conf_time)
            confident_correct += conf_ok
        print(
            f"{begin:>9.1f} {true_onset:>10} {lock_text:>9} {confidence:>7.2f} "
            f"{conf_text:>13} {('yes' if conf_ok else 'no'):>4}"
        )

    print("-" * len(header))
    total = len(begin_points)
    if times:
        within = sum(1 for t in times if t <= args.target_seconds)
        print(f"locked (correct & held {args.hold_seconds}s): {len(times)}/{total}   "
              f"within {args.target_seconds}s: {within}/{total} ({100*within/total:.0f}%)")
        print(f"  lock time: median {np.median(times):.2f}s  mean {np.mean(times):.2f}s  max {np.max(times):.2f}s")
    else:
        print("never locked from any start point")

    if confident_times:
        within = sum(1 for t in confident_times if t <= args.target_seconds)
        print(f"confidence >= {args.min_confidence}: reached {len(confident_times)}/{total}, "
              f"correct when reached {confident_correct}/{len(confident_times)}, "
              f"within {args.target_seconds}s: {within}/{total} ({100*within/total:.0f}%)")
        print(f"  time to confident: median {np.median(confident_times):.2f}s  max {np.max(confident_times):.2f}s")


if __name__ == "__main__":
    main()
