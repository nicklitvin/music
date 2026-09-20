"""CLI: run the vendored basic-pitch model over each recording and cache
its frame-level note/onset activations, so the benchmark can score the
learned note-detection front end without re-running inference every pass.

Uses the same model and windowing as the live path
(app/services/streaming_transcription.py), just over a whole file at once
-- which is exactly the "offline" arm the streaming arm is compared
against in scripts/compare_front_ends.py.

    .venv/Scripts/python scripts/precompute_basic_pitch.py [slug,slug]

Writes content/full/<piece>/_basicpitch.npz (gitignored with the rest of
content/): `activation` and `onset`, both (frames x 88, MIDI 21..108),
plus the frame rate needed to line them up with audio time.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from app.services import benchmark_eval as be  # noqa: E402
from app.services.streaming_transcription import (  # noqa: E402
    MODEL_FFT_HOP,
    MODEL_SAMPLE_RATE,
    load_session,
    transcribe_whole,
)

CONTENT = Path(__file__).resolve().parents[2] / "content" / "full"


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def main() -> None:
    only = set(sys.argv[1].split(",")) if len(sys.argv) > 1 else None
    session = load_session()

    for piece_dir in sorted(CONTENT.iterdir()):
        if not piece_dir.is_dir() or (only and piece_dir.name not in only):
            continue
        wav = piece_dir / "_performance_16k.wav"
        if not wav.exists():
            continue
        out = piece_dir / "_basicpitch.npz"
        if out.exists() and out.stat().st_mtime >= wav.stat().st_mtime:
            log(f"{piece_dir.name}: cached, skipping")
            continue

        started = time.monotonic()
        # The model wants float audio in [-1, 1]; load_wav yields int16 scale.
        audio = be.load_wav(wav) / 32768.0
        note, onset = transcribe_whole(audio, be.RATE, session)
        np.savez_compressed(
            out,
            activation=note.astype(np.float32),
            onset=onset.astype(np.float32),
            frame_rate=np.float64(MODEL_SAMPLE_RATE / MODEL_FFT_HOP),
        )
        log(f"{piece_dir.name}: {note.shape[0]} frames in {time.monotonic() - started:.0f}s -> {out.name}")


if __name__ == "__main__":
    main()
