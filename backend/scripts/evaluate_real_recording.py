"""CLI: score note detection + position tracking on a REAL recording.

Unlike scripts/evaluate_tracking.py, which feeds synthesized audio whose
true score position at every instant is known, this takes a real
performance where it is not. Ground truth is recovered by detecting note
onsets in the recording and string-aligning their detected pitch content
to the score's onsets (page 0 only -- that is all the OMR JSON covers).
The recording may contain later pages; alignment stops at the last page-0
onset and only frames before then are scored.

The alignment is a diagnostic aid, not exact: on the synthesized
reference it agrees with the true onset within +/-2 about 94% of the
time. Treat the numbers as "roughly this bad", not to two decimals.

Decode the recording first (it must be 16 kHz mono 16-bit WAV):
    ffmpeg -i recording.mp3 -ac 1 -ar 16000 -sample_fmt s16 recording.wav

Usage:
    .venv/Scripts/python scripts/evaluate_real_recording.py \
        <recording.wav> <page0_truth.json>
"""

import argparse
import json
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from app.models import NoteBoundingBox  # noqa: E402
from app.services.note_estimation import (  # noqa: E402
    HarmonicSalienceEstimator,
    MIN_MIDI,
    SILENCE_RMS_THRESHOLD,
    pitch_to_midi,
)
from app.services.pitch_detection import _detect_pitches  # noqa: E402
from app.services.position_tracking import (  # noqa: E402
    METHODS,
    TrackerConfig,
    build_tracker,
    sounding_weights,
)
from app.services.score_timeline import TimelineOnset  # noqa: E402

RATE = 16000
HOP = 1200  # 75 ms, the live frame size


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2 or handle.getframerate() != RATE:
            raise SystemExit("Expected 16 kHz mono 16-bit WAV (see the module docstring for the ffmpeg line)")
        raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float64)


def load_timeline(truth: dict) -> list[TimelineOnset]:
    timeline = []
    for entry in truth["onsets"]:
        notes = [
            NoteBoundingBox(
                x=entry["x"], y=entry["y"], width=1, height=1,
                note="quarter", pitch=pitch,
                measureIndex=entry["measureIndex"], pageIndex=truth["pageIndex"],
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


# ---------------------------------------------------------------- onsets
def detect_onsets(audio: np.ndarray, win: int = 1024, hop: int = 160) -> list[int]:
    """Spectral-flux novelty with an adaptive-median threshold. Returns
    sample indices of note onsets."""
    w = np.hanning(win)
    mags = np.array([np.abs(np.fft.rfft(audio[s : s + win] * w)) for s in range(0, len(audio) - win, hop)])
    flux = np.concatenate([[0.0], np.sqrt(np.maximum(np.diff(mags, axis=0), 0.0).sum(axis=1))])
    flux /= flux.max() + 1e-9

    onsets: list[int] = []
    min_gap = int(0.09 * RATE / hop)
    last = -min_gap
    for i in range(1, len(flux) - 1):
        lo, hi = max(0, i - 21), i + 21
        local = np.median(flux[lo:hi]) + 0.12 * np.std(flux[lo:hi])
        if flux[i] > max(local, 0.06) and flux[i] >= flux[i - 1] and flux[i] > flux[i + 1] and i - last >= min_gap:
            onsets.append(i * hop)
            last = i
    return onsets


def pitches_at(audio: np.ndarray, sample: int) -> set[int]:
    """The live detector's output on a 256 ms window just after an onset."""
    window = audio[max(0, sample) : sample + 4096]
    if len(window) < 512 or np.sqrt(np.mean(window**2)) < SILENCE_RMS_THRESHOLD:
        return set()
    return {pitch_to_midi(n) for n in _detect_pitches(window, RATE) if pitch_to_midi(n)}


def align(detected: list[set[int]], score: list[set[int]], gap: float = 0.55) -> list[tuple[int, int]]:
    """DP-align detected-onset pitch sets to every score-onset pitch set,
    consuming all score onsets and a prefix of detected ones. Returns
    matched (detected_index, score_index) anchor pairs."""
    D, S = len(detected), len(score)
    dp = np.full((D + 1, S + 1), -1e9)
    bt = np.zeros((D + 1, S + 1), dtype=np.int8)  # 1 match, 2 skip-detected, 3 skip-score
    dp[0, 0] = 0.0
    for j in range(1, S + 1):
        dp[0, j], bt[0, j] = -gap * j, 3
    for i in range(1, D + 1):
        dp[i, 0], bt[i, 0] = 0.0, 2  # any prefix of detected onsets is free to drop
    for i in range(1, D + 1):
        di = detected[i - 1]
        for j in range(1, S + 1):
            sj = score[j - 1]
            inter = len(di & sj)
            reward = inter / (len(di | sj) or 1) + 0.5 * inter / (len(sj) or 1)
            best, b = dp[i - 1, j - 1] + reward, 1
            if dp[i - 1, j] - gap > best:
                best, b = dp[i - 1, j] - gap, 2
            if dp[i, j - 1] - gap > best:
                best, b = dp[i, j - 1] - gap, 3
            dp[i, j], bt[i, j] = best, b
    i, j = int(np.argmax(dp[:, S])), S
    anchors: list[tuple[int, int]] = []
    while i > 0 or j > 0:
        b = bt[i, j]
        if b == 1:
            anchors.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif b == 2:
            i -= 1
        else:
            j -= 1
    return anchors[::-1]


def onset_time_curve(anchors: list[tuple[int, int]], onset_samples: list[int], n_score: int) -> np.ndarray:
    mx: list[int] = []
    my: list[int] = []
    for k, j in anchors:
        if (not mx or j > mx[-1]) and (not my or onset_samples[k] > my[-1]):
            mx.append(j)
            my.append(onset_samples[k])
    return np.interp(np.arange(n_score), mx, my)


# ---------------------------------------------------------------- metrics
def frame_saliences(audio: np.ndarray):
    est = HarmonicSalienceEstimator()
    return [
        (est.estimate(audio[s : s + HOP]), float(np.sqrt(np.mean(audio[s : s + HOP] ** 2))), s)
        for s in range(0, len(audio) - HOP, HOP)
    ]


def detected_midis(salience: np.ndarray, count: int = 6, threshold: float = 0.3) -> set[int]:
    if not salience.any():
        return set()
    peak = salience.max()
    return {MIN_MIDI + int(i) for i in np.argsort(-salience)[:count] if salience[i] >= peak * threshold}


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def report_detection(frames, truth_index, timeline, end_sample) -> None:
    sounding = [set(sounding_weights(timeline, i)) for i in range(len(timeline))]
    struck = [{pitch_to_midi(p) for p in o.pitches if pitch_to_midi(p)} for o in timeline]
    tp = fp = fn = pctp = pcfp = pcfn = oct_err = n = hi = hipc = hit = 0
    for (sal, rms, s), o in zip(frames, truth_index):
        if s > end_sample or rms < SILENCE_RMS_THRESHOLD or not sounding[o]:
            continue
        n += 1
        det, exp = detected_midis(sal), sounding[o]
        tp += len(det & exp)
        fp += len(det - exp)
        fn += len(exp - det)
        dpc, epc = {m % 12 for m in det}, {m % 12 for m in exp}
        pctp += len(dpc & epc)
        pcfp += len(dpc - epc)
        pcfn += len(epc - dpc)
        oct_err += len({m for m in det if m not in exp and m % 12 in epc})
        if struck[o]:
            hit += 1
            top = max(det) if det else -1
            hi += top == max(struck[o])
            hipc += top % 12 == max(struck[o]) % 12
    p, r, f = prf(tp, fp, fn)
    pp, pr, pf = prf(pctp, pcfp, pcfn)
    print("\nnote detection (HarmonicSalienceEstimator.top_pitches vs what is sounding)")
    print(f"  scored frames             {n}")
    print(f"  exact-pitch  P/R/F1       {p:.2f} / {r:.2f} / {f:.2f}")
    print(f"  pitch-class  P/R/F1       {pp:.2f} / {pr:.2f} / {pf:.2f}")
    if fp:
        print(f"  octave errors / all FPs   {oct_err}/{fp} ({100 * oct_err / fp:.0f}%)")
    print(f"  highest note exact        {hi}/{hit} ({100 * hi / max(hit, 1):.0f}%)")
    print(f"  highest note pitch-class  {hipc}/{hit} ({100 * hipc / max(hit, 1):.0f}%)")


def report_tracking(audio, frames, truth_index, timeline, end_sample) -> None:
    print("\nposition tracking (reported onset vs onset-alignment ground truth)")
    print(f"  {'method':<18}{'exact':>8}{'+/-1':>8}{'+/-2':>8}{'+/-3':>8}{'MAE':>9}")
    for name in METHODS:
        tracker = build_tracker(name, timeline, TrackerConfig())
        errors = []
        for (sal, rms, s), truth in zip(frames, truth_index):
            pos = tracker.observe(audio[s : s + HOP])
            if s <= end_sample and rms >= SILENCE_RMS_THRESHOLD:
                errors.append(pos - truth)
        a = np.abs(np.array(errors))
        print(f"  {name:<18}{100 * np.mean(a == 0):>7.1f}%{100 * np.mean(a <= 1):>7.1f}%"
              f"{100 * np.mean(a <= 2):>7.1f}%{100 * np.mean(a <= 3):>7.1f}%{a.mean():>9.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("wav", type=Path)
    parser.add_argument("truth_json", type=Path, help="page-0 truth JSON (onset pitches + durations)")
    args = parser.parse_args()

    timeline = load_timeline(json.loads(args.truth_json.read_text(encoding="utf-8")))
    score_sets = [{pitch_to_midi(p) for p in o.pitches if pitch_to_midi(p)} for o in timeline]
    audio = load_wav(args.wav)

    onset_samples = detect_onsets(audio)
    detected_sets = [pitches_at(audio, s) for s in onset_samples]
    anchors = align(detected_sets, score_sets)
    if not anchors:
        raise SystemExit("Could not align the recording to the score")
    onset_time = onset_time_curve(anchors, onset_samples, len(score_sets))
    end_sample = int(onset_time[-1]) + 2 * RATE

    last_k, last_j = anchors[-1]
    print(f"{args.wav.name}: {len(audio) / RATE:.1f}s, {len(onset_samples)} note onsets detected")
    print(f"page-0 score: {len(timeline)} onsets; alignment matched {len(anchors)}, "
          f"page 0 ends ~{onset_samples[last_k] / RATE:.1f}s (score onset {last_j})")

    frames = frame_saliences(audio)
    starts = np.asarray(onset_time)
    truth_index = [max(0, min(len(starts) - 1, int(np.searchsorted(starts, s, side="right") - 1))) for _, _, s in frames]

    report_detection(frames, truth_index, timeline, end_sample)
    report_tracking(audio, frames, truth_index, timeline, end_sample)


if __name__ == "__main__":
    main()
