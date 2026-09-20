"""CLI: compare note-detection front ends on the start-anywhere benchmark.

Everything downstream of detection is held identical -- same score, same
tracker, same start points, same lock rule -- so the difference is purely
the evidence the tracker is given:

  harmonic    app/services/note_estimation.py, what ships today: a
              harmonic-salience estimate per frame.
  basicpitch  Spotify's basic-pitch, a small learned transcription CNN,
              precomputed by scripts/precompute_basic_pitch.py. Its
              posteriorgram has a dense noise floor, so values are squared
              before unit-norming to sharpen the contrast.
  attack      basicpitch, plus its onset posteriorgram matched against the
              notes each score onset *strikes*. Sustained pitch content is
              blurry by construction -- a held chord looks the same for a
              second -- while attacks pin a position down. Implemented by
              concatenating [sustain | struck] templates and evidence, so
              the tracker's existing dot product sums both matches.

Needs scripts/precompute_basic_pitch.py to have been run first (it uses
its own virtualenv; see that file). Reads only cached .npz, so nothing
here imports basic-pitch or disturbs the app's pinned dependencies.

    .venv/Scripts/python scripts/compare_front_ends.py [--only slug,slug]
        [--attack-weight 0.35] [--starts 15]
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from app.routers.audio_ws import LIVE_CONFIG, ReportedPosition  # noqa: E402
from app.services import benchmark_eval as be  # noqa: E402
from app.services.note_estimation import MIN_MIDI, MAX_MIDI, pitch_to_midi  # noqa: E402
from app.services.position_markov import MarkovPositionTracker, build_templates  # noqa: E402

CONTENT = Path(__file__).resolve().parents[2] / "content" / "full"
TARGET_SECONDS = be.START_POINT_TARGET_SECONDS
FRONT_ENDS = ("harmonic", "basicpitch", "attack")


def unit(vector: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vector)
    return vector / norm if norm > 0 else vector


def struck_templates(timeline) -> np.ndarray:
    """Unit-norm vector of the pitches actually struck at each onset."""
    out = np.zeros((len(timeline), MAX_MIDI - MIN_MIDI + 1))
    for index, onset in enumerate(timeline):
        for note in onset.notes:
            midi = pitch_to_midi(note.pitch)
            if midi and MIN_MIDI <= midi <= MAX_MIDI:
                out[index, midi - MIN_MIDI] = 1.0
        out[index] = unit(out[index])
    return out


def resample(matrix: np.ndarray, fps: float, frame_samples, sharpen: float = 2.0):
    """basic-pitch frames (~86/s) onto the tracker's 75ms hops."""
    out = []
    for sample in frame_samples:
        lo = int(sample / be.RATE * fps)
        hi = max(lo + 1, int((sample + be.HOP) / be.RATE * fps))
        window = matrix[lo:hi]
        vector = (window.max(axis=0) if len(window) else np.zeros(matrix.shape[1])) ** sharpen
        out.append(unit(vector))
    return out


def evaluate(slug: str, piece_dir: Path, starts_wanted: int, attack_weight: float) -> dict | None:
    cache = piece_dir / "_basicpitch.npz"
    notes_json = piece_dir / "notes.json"
    wav = piece_dir / "_performance_16k.wav"
    if not (cache.exists() and notes_json.exists() and wav.exists()):
        return None

    timeline = be.timeline_from_notes_json(json.loads(notes_json.read_text(encoding="utf-8")), page=None)
    audio = be.load_wav(wav)
    alignment = be.align_recording(audio, timeline)
    if alignment is None:
        return None

    harmonic_frames = be.frame_saliences(audio)
    frame_samples = [s for _, _, s in harmonic_frames]
    truth = be.truth_index_per_frame(alignment.onset_time, frame_samples)
    measures = be.measure_ordinals(timeline)
    starts = be.start_point_samples(alignment, alignment.end_sample, starts_wanted)

    data = np.load(cache)
    fps = float(data["frame_rate"])
    sustain = resample(data["activation"], fps, frame_samples)
    attack = resample(data["onset"], fps, frame_samples)
    sustain_t = build_templates(timeline)
    struck_t = struck_templates(timeline)

    results = {}
    for front_end in FRONT_ENDS:
        if front_end == "harmonic":
            templates, evidence = sustain_t, [f[0] for f in harmonic_frames]
        elif front_end == "basicpitch":
            templates, evidence = sustain_t, sustain
        else:
            weight = attack_weight
            templates = np.hstack([sustain_t * (1 - weight), struck_t * weight])
            evidence = [np.concatenate([s * (1 - weight), a * weight]) for s, a in zip(sustain, attack)]

        within = locked = 0
        blank = np.zeros(be.HOP)
        for begin in starts:
            tracker = MarkovPositionTracker(timeline, LIVE_CONFIG)
            tracker.templates = templates
            reported = ReportedPosition(index=0)
            run, lock = 0, None
            for index, sample in enumerate(frame_samples):
                if sample < begin or sample > alignment.end_sample:
                    continue
                estimate = tracker.observe(blank, salience=evidence[index])
                position = reported.update(
                    estimate.index, estimate.confidence >= LIVE_CONFIG.jump_confidence_gate
                )
                if abs(measures[position] - measures[truth[index]]) <= be.LOCK_MEASURE_TOLERANCE:
                    run += 1
                    if run >= be.LOCK_HOLD_FRAMES:
                        lock = (sample - begin) / be.RATE
                        break
                else:
                    run = 0
            if lock is not None:
                locked += 1
                if lock <= TARGET_SECONDS:
                    within += 1
        results[front_end] = {"within": within, "locked": locked, "total": len(starts)}
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", type=str, default=None)
    parser.add_argument("--starts", type=int, default=15)
    parser.add_argument("--attack-weight", type=float, default=0.35,
                        help="How much of the evidence is note attacks vs sustain (0 = sustain only)")
    args = parser.parse_args()
    only = set(args.only.split(",")) if args.only else None

    totals = {name: [0, 0, 0] for name in FRONT_ENDS}
    started = time.time()
    header = f"{'piece':15s}" + "".join(f"{name:>22s}" for name in FRONT_ENDS)
    print(header)
    print(f"{'':15s}" + "".join(f"{'within5s / locked':>22s}" for _ in FRONT_ENDS))
    for piece_dir in sorted(CONTENT.iterdir()):
        if not piece_dir.is_dir() or (only and piece_dir.name not in only):
            continue
        result = evaluate(piece_dir.name, piece_dir, args.starts, args.attack_weight)
        if result is None:
            continue
        row = f"{piece_dir.name:15s}"
        for name in FRONT_ENDS:
            r = result[name]
            totals[name][0] += r["within"]
            totals[name][1] += r["locked"]
            totals[name][2] += r["total"]
            row += f"{str(r['within']) + '/' + str(r['total']) + '  ' + str(r['locked']) + '/' + str(r['total']):>22s}"
        print(row, flush=True)

    print()
    for name in FRONT_ENDS:
        within, locked, total = totals[name]
        if not total:
            continue
        print(f"{name:12s} within {TARGET_SECONDS:.0f}s: {within}/{total} ({100 * within / total:.0f}%)   "
              f"locks eventually: {locked}/{total} ({100 * locked / total:.0f}%)")
    print(f"[{time.time() - started:.0f}s]")


if __name__ == "__main__":
    main()
