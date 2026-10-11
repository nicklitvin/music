"""A faster stand-in for oemer's neural-net inference (oemer.inference.inference).

The two U-Nets are ~85% of a page's time, and oemer runs them wastefully:
it slides each net's training-size window (256 / 288 px) over the page
every 128 px and averages the overlaps, so the symbol net processes every
pixel about 5 times. A pixel costs the same whatever the tile size, so the
time goes almost entirely on that repetition.

Here, using the any-size copies of the nets (oemer_models):

- The staff net runs on its own 256 px tiles without overlap. Measured on
  four test pages, this gives the same notes as oemer's overlap.
- The symbol net (noteheads, clefs, accidentals) runs on a grid of larger
  tiles fitted to the page, overlapping only by a thin margin that is
  blended linearly so no seam shows. That's ~1.4x the page's pixels
  instead of ~5x, with peak memory well under oemer's at batch 4.

Image loading, resizing and the class-map output are kept identical to
oemer's, so the rest of its pipeline can't tell the difference.
"""

import math
import os

import numpy as np

from app.config import settings
from app.services import oemer_models

STAFF_TILE = 256  # the staff net's training size

_sessions: dict[str, object] = {}


def _session(model_path: str):
    """One onnxruntime session per net, kept for the life of the worker
    process -- a sheet's pages are processed one after another in it."""
    import onnxruntime as rt

    if model_path not in _sessions:
        options = rt.SessionOptions()
        options.intra_op_num_threads = settings.omr_threads or os.cpu_count() or 1
        options.inter_op_num_threads = 1
        options.log_severity_level = 3  # onnx's "unused initializer" chatter
        _sessions[model_path] = rt.InferenceSession(
            oemer_models.any_size_path(model_path), options, providers=["CPUExecutionProvider"]
        )
    return _sessions[model_path]


def _load_image(img_path: str) -> np.ndarray:
    # As oemer.inference.inference loads it, down to the cv2 round trip
    # (which leaves the channels in BGR order -- the order the nets saw in
    # training).
    import cv2
    from oemer.inference import resize_image
    from PIL import Image

    image = Image.open(img_path)
    if image.format != "GIF":
        image = Image.fromarray(cv2.imread(img_path))
    return np.array(resize_image(image.convert("RGB")))


def tile_starts(length: int, tile: int, step: int) -> list[int]:
    """oemer's own layout: a tile every `step`, the last one flush with the end."""
    if length <= tile:
        return [0]
    return list(range(0, length - tile, step)) + [length - tile]


def fit_grid(length: int, target: int, overlap: int) -> tuple[int, list[int]]:
    """The fewest tiles of about `target` px (a multiple of 32: each net
    halves the size five times) that cover `length` with neighbours sharing
    at least `overlap` px. Returns the tile size and each tile's start."""
    if length <= target:
        return math.ceil(length / 32) * 32, [0]
    count = math.ceil((length - overlap) / (target - overlap))
    tile = math.ceil((length + (count - 1) * overlap) / count / 32) * 32
    return tile, [round(i * (length - tile) / (count - 1)) for i in range(count)]


def _ramp(size: int, overlap: int) -> np.ndarray:
    """Per-pixel weight across a tile: rising linearly over the overlap at
    each edge, so a pixel shared by two tiles fades from one to the other."""
    edge = np.minimum(np.arange(size) + 1, np.arange(size)[::-1] + 1).astype(np.float32)
    return np.minimum(edge / max(1, overlap), 1.0)


def _pad_to(image: np.ndarray, height: int, width: int) -> np.ndarray:
    if image.shape[0] >= height and image.shape[1] >= width:
        return image
    padded = np.full((max(height, image.shape[0]), max(width, image.shape[1]), 3), 255, np.uint8)
    padded[: image.shape[0], : image.shape[1]] = image
    return padded


def predict_staff(run, image: np.ndarray, batch_size: int) -> np.ndarray:
    """Non-overlapping 256 px tiles (the last row/column flush with the
    edge, as oemer lays them out), `batch_size` tiles per run."""
    height, width = image.shape[:2]
    image = _pad_to(image, STAFF_TILE, STAFF_TILE)
    spots = [
        (y, x)
        for y in tile_starts(image.shape[0], STAFF_TILE, STAFF_TILE)
        for x in tile_starts(image.shape[1], STAFF_TILE, STAFF_TILE)
    ]
    out = weight = None
    for i in range(0, len(spots), batch_size):
        chunk = spots[i : i + batch_size]
        batch = np.stack([image[y : y + STAFF_TILE, x : x + STAFF_TILE] for y, x in chunk])
        for (y, x), pred in zip(chunk, run(batch)):
            if out is None:
                out = np.zeros(image.shape[:2] + (pred.shape[-1],), np.float32)
                weight = np.zeros(image.shape[:2] + (1,), np.float32)
            out[y : y + STAFF_TILE, x : x + STAFF_TILE] += pred
            weight[y : y + STAFF_TILE, x : x + STAFF_TILE] += 1
    return (out / weight)[:height, :width]


def predict_grid(run, image: np.ndarray, target: int, overlap: int) -> np.ndarray:
    """A grid of ~`target` px tiles fitted to the image, blended over their
    `overlap`."""
    height, width = image.shape[:2]
    tile_h, ys = fit_grid(height, target, overlap)
    tile_w, xs = fit_grid(width, target, overlap)
    image = _pad_to(image, tile_h, tile_w)
    blend = (_ramp(tile_h, overlap)[:, None] * _ramp(tile_w, overlap)[None, :])[..., None]
    out = None
    weight = np.zeros(image.shape[:2] + (1,), np.float32)
    for y in ys:
        for x in xs:
            pred = run(image[None, y : y + tile_h, x : x + tile_w])[0]
            if out is None:
                out = np.zeros(image.shape[:2] + (pred.shape[-1],), np.float32)
            out[y : y + tile_h, x : x + tile_w] += pred * blend
            weight[y : y + tile_h, x : x + tile_w] += blend
    return (out / weight)[:height, :width]


def inference(model_path: str, img_path: str, step_size=None, batch_size=None, manual_th=None, use_tf=False):
    """Same arguments and (class_map, probabilities) result as oemer's
    `inference`; `step_size` and `batch_size` are oemer's knobs and unused."""
    session = _session(model_path)

    def run(batch: np.ndarray) -> np.ndarray:
        return session.run(None, {"input": batch})[0]

    image = _load_image(img_path)
    if os.path.basename(os.path.normpath(model_path)) == "unet_big":
        out = predict_staff(run, image, settings.omr_batch_size)
    else:
        out = predict_grid(run, image, settings.omr_tile_size, settings.omr_tile_overlap)

    if manual_th is None:
        return np.argmax(out, axis=-1), out
    class_map = np.zeros(out.shape[:2] + (len(manual_th),))
    for idx, threshold in enumerate(manual_th):
        class_map[..., idx] = np.where(out[..., idx + 1] > threshold, 1, 0)
    return class_map, out
