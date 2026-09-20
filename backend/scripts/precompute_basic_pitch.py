"""CLI: run Spotify's basic-pitch over each recording and cache its
frame-level note activations for the benchmark to use as an alternative
note-detection front end.

Runs in its own virtualenv (.venv-transcribe), NOT the app's: basic-pitch
wants a modern numpy, while the app pins an older numpy/scipy/onnxruntime
set so oemer's pretrained models still load (see requirements.txt). Keeping
them apart is the whole point of precomputing to disk -- the benchmark then
reads plain .npy files with no basic-pitch import anywhere near the app.

    .venv-transcribe/Scripts/python scripts/precompute_basic_pitch.py

Writes content/full/<piece>/_basicpitch.npz (gitignored with the rest of
content/): `activation` (frames x 88, MIDI 21..108) and `onset` (same
shape), plus the frame rate needed to line them up with audio time.
"""

import sys
import time
from pathlib import Path

import numpy as np

CONTENT = Path(__file__).resolve().parents[2] / "content" / "full"


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def main() -> None:
    from basic_pitch.constants import AUDIO_SAMPLE_RATE, FFT_HOP
    from basic_pitch.inference import predict

    frame_rate = AUDIO_SAMPLE_RATE / FFT_HOP
    only = set(sys.argv[1].split(",")) if len(sys.argv) > 1 else None

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
        model_output, _, _ = predict(str(wav))
        np.savez_compressed(
            out,
            activation=model_output["note"].astype(np.float32),
            onset=model_output["onset"].astype(np.float32),
            frame_rate=np.float64(frame_rate),
        )
        log(f"{piece_dir.name}: {model_output['note'].shape[0]} frames "
            f"in {time.monotonic() - started:.0f}s -> {out.name}")


if __name__ == "__main__":
    main()
