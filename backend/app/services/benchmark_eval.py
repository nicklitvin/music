"""Reusable evaluation core shared by scripts/evaluate_real_recording.py
(human-readable CLI) and scripts/run_benchmarks.py (batch runner that
produces the committed JSON `GET /api/benchmarks` serves for local
inspection -- see backend/README for why nothing in the UI shows it).

Every evaluation here works from a REAL recording: since a real
performance's true position is not known up front, it is recovered by
detecting note onsets in the recording and string-aligning their detected
pitch content to the score's onsets (see `align_recording`). This is a
diagnostic aid, not exact ground truth -- treat the numbers as "roughly
this good/bad". (An earlier version of this module also scored synthesized
audio with exactly-known ground truth; that machinery -- and the
synthesized samples it evaluated -- was retired in favor of testing
against real recordings from many start points instead, which is what the
product actually needs to get right. See `evaluate_start_points`.)
"""

from __future__ import annotations

import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app.models import NoteBoundingBox
from app.routers.audio_ws import LIVE_CONFIG, ReportedPosition
from app.services.note_estimation import (
    HarmonicSalienceEstimator,
    MIN_MIDI,
    SILENCE_RMS_THRESHOLD,
    pitch_to_midi,
)
from app.services.pitch_detection import _detect_pitches
from app.services.position_markov import MarkovPositionTracker
from app.services.position_tracking import METHODS, TrackerConfig, build_tracker, sounding_weights
from app.services.score_timeline import TimelineOnset, build_timeline

RATE = 16000
HOP = 1200  # 75 ms, the live frame size


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2 or handle.getframerate() != RATE:
            raise ValueError(f"{path}: expected 16 kHz mono 16-bit WAV")
        raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float64)


def timeline_from_notes_json(notes: list[dict], page: int | None = 0, tempo_bpm: float = 99.0) -> list[TimelineOnset]:
    """From raw OMR note bounding boxes (extract_notes.py output): lays
    them out in time from each note's own type/duration.

    `page=None` uses every page present in the file, laid out back to back
    in reading order -- which is what a recording of the whole piece needs
    to align against. A single page only covers the first minute or so of a
    five-minute recording, so everything after that has no score material
    to be tracked against at all.
    """
    boxes = [NoteBoundingBox(**n) for n in notes if page is None or n.get("pageIndex", 0) == page]
    return build_timeline(boxes, tempo_bpm=tempo_bpm)


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


def align_pitch_strings(detected: list[set[int]], score: list[set[int]], gap: float = 0.55) -> list[tuple[int, int]]:
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


@dataclass
class Alignment:
    onset_time: np.ndarray  # sample index of each score onset
    end_sample: int  # last sample belonging to this page's material
    detected_onsets: int
    matched_onsets: int
    last_matched_time_seconds: float
    # Sample index of every note onset detected in the audio -- i.e. every
    # moment something is actually being played. Start-point selection uses
    # these so a cold start is never asked to find its place from silence.
    onset_samples: list[int] = field(default_factory=list)


def align_recording(audio: np.ndarray, timeline: list[TimelineOnset]) -> Alignment | None:
    score_sets = [{pitch_to_midi(p) for p in o.pitches if pitch_to_midi(p)} for o in timeline]
    onset_samples = detect_onsets(audio)
    detected_sets = [pitches_at(audio, s) for s in onset_samples]
    anchors = align_pitch_strings(detected_sets, score_sets)
    if not anchors:
        return None
    onset_time = onset_time_curve(anchors, onset_samples, len(score_sets))
    end_sample = int(onset_time[-1]) + 2 * RATE
    last_k, _ = anchors[-1]
    return Alignment(
        onset_time=onset_time,
        end_sample=end_sample,
        detected_onsets=len(onset_samples),
        matched_onsets=len(anchors),
        last_matched_time_seconds=onset_samples[last_k] / RATE,
        onset_samples=onset_samples,
    )


def truth_index_per_frame(onset_time: np.ndarray, frame_samples: list[int]) -> list[int]:
    return [
        max(0, min(len(onset_time) - 1, int(np.searchsorted(onset_time, s, side="right") - 1)))
        for s in frame_samples
    ]


# ---------------------------------------------------------------- metrics
def frame_saliences(audio: np.ndarray) -> list[tuple[np.ndarray, float, int]]:
    """(salience, rms, sample_index) for every 75ms hop."""
    est = HarmonicSalienceEstimator()
    out = []
    for s in range(0, len(audio) - HOP, HOP):
        frame = audio[s : s + HOP]
        out.append((est.estimate(frame), float(np.sqrt(np.mean(frame**2))), s))
    return out


def detected_midis(salience: np.ndarray, count: int = 6, threshold: float = 0.3) -> set[int]:
    if not salience.any():
        return set()
    peak = salience.max()
    return {MIN_MIDI + int(i) for i in np.argsort(-salience)[:count] if salience[i] >= peak * threshold}


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def evaluate_detection(frames, truth_index, timeline: list[TimelineOnset], end_sample: int) -> dict:
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
    return {
        "scoredFrames": n,
        "exactPitch": {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f, 3)},
        "pitchClass": {"precision": round(pp, 3), "recall": round(pr, 3), "f1": round(pf, 3)},
        "octaveErrorShareOfFalsePositives": round(oct_err / fp, 3) if fp else None,
        "highestNoteExact": round(hi / hit, 3) if hit else None,
        "highestNotePitchClass": round(hipc / hit, 3) if hit else None,
    }


def evaluate_tracking_methods(audio, frames, truth_index, timeline: list[TimelineOnset], end_sample: int) -> dict:
    results = {}
    for name in METHODS:
        tracker = build_tracker(name, timeline, TrackerConfig())
        errors = []
        for (_, rms, s), truth in zip(frames, truth_index):
            pos = tracker.observe(audio[s : s + HOP])
            if s <= end_sample and rms >= SILENCE_RMS_THRESHOLD:
                errors.append(pos - truth)
        if not errors:
            results[name] = None
            continue
        a = np.abs(np.array(errors))
        results[name] = {
            "exact": round(100 * float(np.mean(a == 0)), 1),
            "within1": round(100 * float(np.mean(a <= 1)), 1),
            "within2": round(100 * float(np.mean(a <= 2)), 1),
            "within3": round(100 * float(np.mean(a <= 3)), 1),
            "meanAbsoluteError": round(float(a.mean()), 2),
        }
    return results


def _live_run(audio, frames, truth_index, timeline, end_sample, begin_sample, *, seed_hint: bool = True):
    tracker = MarkovPositionTracker(timeline, LIVE_CONFIG)
    if seed_hint:
        tracker.apply_hint(0, strength=0.9, width=3.0)
    reported = ReportedPosition(index=0)
    positions, jumps, errors = [], [], []
    prev, run, lock = 0, 0, None
    for (_, rms, s), ti in zip(frames, truth_index):
        if s < begin_sample or s > end_sample:
            continue
        pos = reported.update(tracker.observe(audio[s : s + HOP]).index)
        if positions:
            jumps.append(abs(pos - prev))
        if rms >= SILENCE_RMS_THRESHOLD:
            errors.append(abs(pos - ti))
        if lock is None and abs(pos - ti) <= 3:
            run += 1
            if run >= 8:
                lock = (s - begin_sample) / RATE
        elif lock is None:
            run = 0
        positions.append(pos)
        prev = pos
    return positions, np.array(jumps), np.array(errors), lock


def evaluate_live_path(audio, frames, truth_index, timeline: list[TimelineOnset], alignment: "Alignment") -> dict:
    """The actual live path a listener sees: LIVE_CONFIG Markov tracker
    seeded at onset 0, reported position rate-limited."""
    end_sample = alignment.end_sample
    positions, jumps, errors, _ = _live_run(audio, frames, truth_index, timeline, end_sample, 0)
    playthrough = {
        "finalOnset": int(positions[-1]),
        "totalOnsets": len(timeline),
        "largestSingleFrameMove": int(jumps.max()) if len(jumps) else 0,
        "movesOverThreeOnsets": int((jumps > 3).sum()) if len(jumps) else 0,
        "meanAbsoluteError": round(float(errors.mean()), 2) if len(errors) else None,
        "within3": round(100 * float(np.mean(errors <= 3)), 1) if len(errors) else None,
        "maxError": int(errors.max()) if len(errors) else None,
    }
    return {"playthrough": playthrough, "startPoints": evaluate_start_points(audio, frames, truth_index, timeline, alignment)}


# "A few seconds" -- the target this whole evaluation is judged against: a
# reader who starts playing at an arbitrary point on the page should be
# found and correctly tracked within this long, most of the time.
START_POINT_TARGET_SECONDS = 5.0


def start_point_samples(alignment: "Alignment", end_sample: int, n_starts: int, tail_seconds: float = 6.0) -> list[int]:
    """Where to try starting from: moments a note is actually struck.

    Spread evenly over the recording's *detected onsets* rather than over
    the clock, so every start point is a real "the player starts playing
    here" moment. Picking by clock instead lands starts in rests, page
    turns and held chords, where there is nothing to identify a position
    from and the tracker is being blamed for silence.
    """
    latest = end_sample - int(tail_seconds * RATE)  # leave room to actually lock
    playable = [s for s in alignment.onset_samples if s <= latest]
    if not playable:
        return []
    picks = np.linspace(0, len(playable) - 1, min(n_starts, len(playable)))
    return sorted({playable[int(round(i))] for i in picks})


def evaluate_start_points(
    audio,
    frames,
    truth_index,
    timeline: list[TimelineOnset],
    alignment: "Alignment",
    n_starts: int = 15,
    target_seconds: float = START_POINT_TARGET_SECONDS,
) -> dict:
    """How well a truly cold tracker (no seed -- the reader may have started
    playing anywhere in the piece) finds and locks onto the correct
    position, tried from many points spread across the whole matched
    recording.

    This is what "80% correct within a few seconds, from any moment" (the
    actual product requirement) means as a number: for each start point, a
    fresh tracker is fed audio beginning there with no hint at all, and we
    record how long -- if ever -- it takes for the reported position to
    become correct and *stay* correct (a single lucky frame doesn't count;
    see `_live_run`'s own lock logic, which requires 8 consecutive frames
    within +/-3 onsets of ground truth -- close enough to be "the right
    spot on the page" per this module's convention throughout).
    """
    end_sample = alignment.end_sample
    if end_sample / RATE < 8.0:
        return {"targetSeconds": target_seconds, "results": [], "lockedCount": 0, "withinTarget": 0, "total": 0, "withinTargetPct": None}

    results = []
    for begin_sample in start_point_samples(alignment, end_sample, n_starts):
        _, _, _, lock = _live_run(audio, frames, truth_index, timeline, end_sample, begin_sample, seed_hint=False)
        results.append({
            "startSeconds": round(begin_sample / RATE, 1),
            "locksAfterSeconds": round(lock, 2) if lock is not None else None,
        })

    locked = [r for r in results if r["locksAfterSeconds"] is not None]
    within = [r for r in locked if r["locksAfterSeconds"] <= target_seconds]
    return {
        "targetSeconds": target_seconds,
        "results": results,
        "lockedCount": len(locked),
        "withinTarget": len(within),
        "total": len(results),
        "withinTargetPct": round(100 * len(within) / len(results), 1) if results else None,
    }


@dataclass
class PieceEvaluation:
    durationSeconds: float
    detectedOnsets: int
    matchedOnsets: int
    totalScoreOnsets: int
    lastMatchedTimeSeconds: float
    detection: dict
    tracking: dict
    livePath: dict
    warnings: list[str] = field(default_factory=list)


def evaluate_piece(audio: np.ndarray, timeline: list[TimelineOnset]) -> PieceEvaluation | None:
    """Full real-recording evaluation: alignment, note detection, every
    tracking method, and the live path. None if the recording could not be
    aligned to the score at all (e.g. it doesn't actually contain this
    piece)."""
    alignment = align_recording(audio, timeline)
    if alignment is None:
        return None

    frames = frame_saliences(audio)
    truth_index = truth_index_per_frame(alignment.onset_time, [s for _, _, s in frames])

    warnings = []
    if alignment.matched_onsets < 0.8 * len(timeline):
        warnings.append(
            f"only matched {alignment.matched_onsets}/{len(timeline)} score onsets to the recording -- "
            "alignment (and everything below) may be unreliable"
        )

    return PieceEvaluation(
        durationSeconds=round(len(audio) / RATE, 1),
        detectedOnsets=alignment.detected_onsets,
        matchedOnsets=alignment.matched_onsets,
        totalScoreOnsets=len(timeline),
        lastMatchedTimeSeconds=round(alignment.last_matched_time_seconds, 1),
        detection=evaluate_detection(frames, truth_index, timeline, alignment.end_sample),
        tracking=evaluate_tracking_methods(audio, frames, truth_index, timeline, alignment.end_sample),
        livePath=evaluate_live_path(audio, frames, truth_index, timeline, alignment),
        warnings=warnings,
    )


