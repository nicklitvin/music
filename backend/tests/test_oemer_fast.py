import cv2
import numpy as np
import pytest
import scipy.ndimage

from app.services.oemer_fast import label_boxes, where_in_box


@pytest.fixture
def labels():
    rng = np.random.default_rng(0)
    blobs = scipy.ndimage.binary_opening(rng.random((300, 400)) > 0.6)
    label_map, count = scipy.ndimage.label(blobs)
    return label_map, count


def test_searching_a_labels_box_finds_what_searching_the_page_does(labels):
    label_map, count = labels
    boxes = label_boxes(label_map)
    for value in range(1, count + 1):
        ys, xs = where_in_box(label_map, value, boxes[value])
        full_ys, full_xs = np.where(label_map == value)
        # Same pixels, same order -- oemer's code extends lists from these.
        np.testing.assert_array_equal(ys, full_ys)
        np.testing.assert_array_equal(xs, full_xs)


def test_maps_that_use_minus_one_for_none(labels):
    label_map, count = labels
    ids = label_map - 1  # -1 = none, 0.. = ids, like oemer's note_id map
    boxes = label_boxes(ids, offset=1)
    for value in range(count):
        ys, xs = where_in_box(ids, value, boxes[value + 1])
        full_ys, full_xs = np.where(ids == value)
        np.testing.assert_array_equal(ys, full_ys)
        np.testing.assert_array_equal(xs, full_xs)


def test_a_missing_label_has_no_pixels():
    ys, xs = where_in_box(np.zeros((5, 5), int), 3, None)
    assert len(ys) == len(xs) == 0


@pytest.mark.parametrize("seed", range(20))
def test_filling_a_box_in_a_window_gives_the_pixels_filling_the_page_does(seed):
    # parse_beams relies on this: OpenCV's polygon fill is unchanged by a
    # whole-pixel shift, including where the polygon runs off the page.
    rng = np.random.default_rng(seed)
    height, width = 120, 160
    rect = ((rng.uniform(0, width + 20), rng.uniform(0, height + 20)), (rng.uniform(2, 60), rng.uniform(2, 15)), rng.uniform(0, 180))
    points = cv2.boxPoints(rect)
    if (points < 0).any():
        pytest.skip("oemer skips boxes with negative corners")
    points = points.astype(np.uint64).astype(np.int64)

    page = np.zeros((height, width), np.uint8)
    cv2.fillPoly(page, [points.astype(np.int32)], color=1)

    x0, y0 = points.min(axis=0)
    x1, y1 = min(points[:, 0].max() + 1, width), min(points[:, 1].max() + 1, height)
    window = np.zeros((max(0, y1 - y0), max(0, x1 - x0)), np.uint8)
    if window.size:  # as parse_beams: a box wholly off the page fills nothing
        cv2.fillPoly(window, [(points - (x0, y0)).astype(np.int32)], color=1)
    ys, xs = np.where(window == 1)

    full_ys, full_xs = np.where(page == 1)
    np.testing.assert_array_equal(ys + y0, full_ys)
    np.testing.assert_array_equal(xs + x0, full_xs)
