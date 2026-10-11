import os

import numpy as np
import pytest

from app.services import oemer_inference, oemer_models
from app.services.oemer_inference import fit_grid, predict_grid, predict_staff


@pytest.mark.parametrize("length", [100, 384, 385, 1000, 1610, 2280, 2295])
@pytest.mark.parametrize("target,overlap", [(384, 64), (640, 96)])
def test_grid_tiles_cover_the_page_with_at_least_the_overlap(length, target, overlap):
    tile, starts = fit_grid(length, target, overlap)

    assert tile % 32 == 0  # the nets halve the size five times
    assert starts[0] == 0
    if length > target:
        assert starts[-1] + tile == length
        assert all(b - a <= tile - overlap for a, b in zip(starts, starts[1:]))
        assert tile <= target + 32
    else:
        assert tile >= length  # one tile, padded


def per_pixel(batch):
    # Stands in for a net: any function of each pixel alone. Tiling and
    # blending can't change its output, so the stitched result must match
    # running it on the whole image at once.
    x = batch.astype(np.float32)
    return np.stack([x[..., 0] / 255, x[..., 1] * x[..., 2] / 255**2], axis=-1)


@pytest.fixture
def page():
    rng = np.random.default_rng(0)
    return rng.integers(0, 256, (1000, 700, 3), dtype=np.uint8)


def test_the_symbol_grid_stitches_back_into_the_whole_page(page):
    out = predict_grid(per_pixel, page, target=384, overlap=64)
    np.testing.assert_allclose(out, per_pixel(page[None])[0], rtol=1e-5)


def test_the_staff_tiles_stitch_back_into_the_whole_page(page):
    out = predict_staff(per_pixel, page, batch_size=3)
    np.testing.assert_allclose(out, per_pixel(page[None])[0], rtol=1e-5)


def test_a_page_smaller_than_a_tile_is_padded_and_cropped_back():
    small = np.full((100, 150, 3), 7, np.uint8)
    assert predict_grid(per_pixel, small, 384, 64).shape == (100, 150, 2)
    assert predict_staff(per_pixel, small, 4).shape == (100, 150, 2)


def test_inference_sends_each_net_its_own_tiling(monkeypatch, page):
    calls = []

    class FakeSession:
        def run(self, _outputs, feed):
            calls.append(feed["input"].shape)
            return [per_pixel(feed["input"])]

    monkeypatch.setattr(oemer_inference, "_session", lambda path: FakeSession())
    monkeypatch.setattr(oemer_inference, "_load_image", lambda path: page)
    monkeypatch.setattr(oemer_inference.settings, "omr_batch_size", 2)

    class_map, out = oemer_inference.inference("checkpoints/unet_big", "page.png")
    assert {shape[1:3] for shape in calls} == {(256, 256)}
    assert max(shape[0] for shape in calls) == 2
    np.testing.assert_array_equal(class_map, np.argmax(per_pixel(page[None])[0], axis=-1))

    calls.clear()
    oemer_inference.inference(os.path.join("checkpoints", "seg_net"), "page.png")
    assert all(shape[0] == 1 and shape[1] > 256 for shape in calls)


def fixed_size_layer_norm_model(path, size):
    """The shape of what tf2onnx made of oemer's layer norms: a reshape to
    [1, N*H*W, C, 1] with H and W baked in as constants, then back."""
    import onnx
    from onnx import TensorProto, helper

    prefix = "model/layer_normalization"
    nodes = [
        helper.make_node("Shape", ["input"], [f"{prefix}/Shape:0"], name=f"{prefix}/Shape"),
        helper.make_node("Cast", [f"{prefix}/Shape:0"], ["shape32"], name=f"{prefix}/Shape__1", to=TensorProto.INT32),
        helper.make_node("Slice", ["shape32"], ["n1"], name=f"{prefix}/strided_slice", axes=[0], starts=[0], ends=[1]),
        helper.make_node("Squeeze", ["n1"], ["n"], name=f"{prefix}/strided_slice__2", axes=[0]),
        helper.make_node("Mul", ["n", "size"], ["nh"], name=f"{prefix}/mul_1"),
        helper.make_node("Mul", ["nh", "size"], ["nhw"], name=f"{prefix}/mul_2"),
        helper.make_node("Unsqueeze", ["nhw"], ["packed"], name=f"{prefix}/packed", axes=[0]),
        helper.make_node("Concat", ["c_one", "packed", "c_channels", "c_one"], ["flat32"], name=f"{prefix}/concat", axis=0),
        helper.make_node("Cast", ["flat32"], ["flat"], name=f"{prefix}/cast_flat", to=TensorProto.INT64),
        helper.make_node("Reshape", ["input", "flat"], ["flattened"], name=f"{prefix}/Reshape"),
        helper.make_node("Cast", ["shape32"], ["shape64"], name=f"{prefix}/cast_back", to=TensorProto.INT64),
        helper.make_node("Reshape", ["flattened", "shape64"], ["output"], name=f"{prefix}/Reshape_1"),
    ]
    graph = helper.make_graph(
        nodes,
        "layer_norm",
        [helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, size, size, 2])],
        [helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, size, size, 2])],
        initializer=[
            helper.make_tensor("size", TensorProto.INT32, [], [size]),
            helper.make_tensor("c_one", TensorProto.INT32, [1], [1]),
            helper.make_tensor("c_channels", TensorProto.INT32, [1], [2]),
        ],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 9)])
    model.ir_version = 7  # as old as oemer's, and loadable by the pinned onnxruntime
    onnx.save(model, path)


def run_model(path, x):
    import onnxruntime as rt

    session = rt.InferenceSession(path, providers=["CPUExecutionProvider"])
    return session.run(None, {"input": x})[0]


def test_the_any_size_copy_runs_on_other_sizes(tmp_path):
    fixed = str(tmp_path / "model.onnx")
    fixed_size_layer_norm_model(fixed, 4)
    bigger = np.arange(1 * 8 * 6 * 2, dtype=np.float32).reshape(1, 8, 6, 2)
    with pytest.raises(Exception):
        run_model(fixed, bigger)

    any_size = oemer_models.any_size_path(str(tmp_path))

    assert oemer_models.make_any_size(fixed, str(tmp_path / "again.onnx")) == 2
    np.testing.assert_array_equal(run_model(any_size, bigger), bigger)
    same = np.ones((1, 4, 4, 2), np.float32)
    np.testing.assert_array_equal(run_model(any_size, same), run_model(fixed, same))


@pytest.mark.parametrize("name,size", [("unet_big", 256), ("seg_net", 288)])
def test_the_any_size_copies_of_oemers_nets_match_them_exactly(tmp_path, name, size):
    # Only where oemer's weights have been downloaded (they're ~100 MB).
    import shutil

    import oemer

    source = os.path.join(oemer.MODULE_PATH, "checkpoints", name, "model.onnx")
    if not os.path.exists(source):
        pytest.skip("oemer's checkpoints aren't downloaded")
    shutil.copy(source, tmp_path / "model.onnx")

    any_size = oemer_models.any_size_path(str(tmp_path))
    tiles = np.random.default_rng(1).integers(0, 256, (2, size, size, 3), dtype=np.uint8)
    np.testing.assert_array_equal(run_model(any_size, tiles), run_model(source, tiles))
    assert run_model(any_size, tiles[:1, :, : size - 32]).shape[1:3] == (size, size - 32)
