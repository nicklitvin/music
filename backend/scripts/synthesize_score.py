"""CLI: render a notes JSON (from extract_notes.py) to a piano WAV plus a
ground-truth timeline.

Gives the position-tracking algorithms a reproducible input whose correct
answer is known exactly at every instant -- unlike a real microphone
recording, where "which note was actually sounding at t=12.3s" is a guess.
The WAV and the ground truth come from the same timeline, so any accuracy
number measured against it is meaningful.

Uses pretty_midi to sequence and synthesize. The default waveform is a sum
of harmonics with piano-like relative amplitudes rather than a pure sine --
a sine would make pitch detection unrealistically easy, since the whole
difficulty with real instruments is their overtones.

Usage:
    .venv/Scripts/python scripts/synthesize_score.py <notes_json> [--page N]
        [--tempo 99] [--out-wav path.wav] [--out-truth path.json]
"""

import argparse
import json
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pretty_midi  # noqa: E402

from app.models import NoteBoundingBox  # noqa: E402
from app.services.score_timeline import build_timeline  # noqa: E402

SAMPLE_RATE = 16000  # matches the live audio pipeline's rate
ACOUSTIC_GRAND_PIANO = 0

# Relative amplitudes of harmonics 1..6, roughly piano-like: strong
# fundamental, quickly falling overtones.
HARMONIC_AMPLITUDES = (1.0, 0.45, 0.22, 0.12, 0.07, 0.04)


def piano_wave(phase: np.ndarray) -> np.ndarray:
    out = np.zeros_like(phase)
    for index, amplitude in enumerate(HARMONIC_AMPLITUDES, start=1):
        out += amplitude * np.sin(index * phase)
    return out / sum(HARMONIC_AMPLITUDES)


def write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    peak = np.max(np.abs(samples))
    normalized = samples / peak if peak > 0 else samples
    pcm16 = (normalized * 32767 * 0.9).astype("<i2")

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm16.tobytes())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("notes_json", type=Path)
    parser.add_argument("--page", type=int, default=0)
    parser.add_argument("--tempo", type=float, default=99.0, help="Quarter notes per minute (default: 99)")
    parser.add_argument("--out-wav", type=Path, default=None)
    parser.add_argument("--out-truth", type=Path, default=None)
    args = parser.parse_args()

    out_wav = args.out_wav or args.notes_json.with_name(f"{args.notes_json.stem}-page{args.page}.wav")
    out_truth = args.out_truth or args.notes_json.with_name(f"{args.notes_json.stem}-page{args.page}-truth.json")

    raw = json.loads(args.notes_json.read_text(encoding="utf-8"))
    notes = [NoteBoundingBox(**entry) for entry in raw if entry["pageIndex"] == args.page]
    if not notes:
        raise SystemExit(f"No notes for page {args.page} in {args.notes_json}")

    timeline = build_timeline(notes, tempo_bpm=args.tempo)
    print(f"{len(notes)} notes -> {len(timeline)} onsets", file=sys.stderr)

    midi = pretty_midi.PrettyMIDI(initial_tempo=args.tempo)
    piano = pretty_midi.Instrument(program=ACOUSTIC_GRAND_PIANO)
    for onset in timeline:
        for note, duration in zip(onset.notes, onset.note_durations):
            number = pretty_midi.note_name_to_number(note.pitch)
            piano.notes.append(
                pretty_midi.Note(
                    velocity=90,
                    pitch=number,
                    start=onset.start_seconds,
                    end=onset.start_seconds + duration,
                )
            )
    midi.instruments.append(piano)

    samples = piano.synthesize(fs=SAMPLE_RATE, wave=piano_wave)
    write_wav(out_wav, samples, SAMPLE_RATE)
    duration_seconds = len(samples) / SAMPLE_RATE
    print(f"Wrote {out_wav} ({duration_seconds:.1f}s, {len(piano.notes)} midi notes)", file=sys.stderr)

    truth = {
        "sourceNotesJson": args.notes_json.name,
        "pageIndex": args.page,
        "tempoBpm": args.tempo,
        "sampleRate": SAMPLE_RATE,
        "durationSeconds": duration_seconds,
        "onsets": [
            {
                "index": onset.index,
                "startSeconds": onset.start_seconds,
                "advanceSeconds": onset.advance_seconds,
                "pitches": onset.pitches,
                # Per-note sounding lengths, parallel to `pitches`. A note
                # often outlasts advanceSeconds (a held bass note under
                # faster melody notes), so consumers need these to know
                # what is still ringing at any later onset.
                "durations": [round(d, 6) for d in onset.note_durations],
                "highestPitch": max(onset.pitches, key=pretty_midi.note_name_to_number),
                "measureIndex": onset.notes[0].measureIndex,
                "x": onset.notes[0].x,
                "y": min(note.y for note in onset.notes),
            }
            for onset in timeline
        ],
    }
    out_truth.write_text(json.dumps(truth, indent=2), encoding="utf-8")
    print(f"Wrote {out_truth} ({len(timeline)} onsets)", file=sys.stderr)


if __name__ == "__main__":
    main()
