"""Copies of oemer's two U-Nets that accept any tile size.

oemer's networks were exported from TensorFlow with the training tile size
(256 px for the staff net, 288 px for the symbol net) baked into every
layer normalization: tf2onnx lowered each Keras LayerNormalization to a
reshape to [1, N*H*W, C, 1] plus a BatchNormalization, with H and W as
constants. The normalization itself is per pixel (over channels), so that
constant is the only thing tying a net to its tile size. Swapping it for
the input's actual height and width gives a net whose output is identical
at the original size (checked bit for bit) and that runs on bigger tiles
-- which is what lets oemer_inference stop processing every pixel ~5 times
over.

The copies are model assets derived from oemer's own downloaded weights,
cached beside them in the installed package (like the weights themselves),
not user data.
"""

import os
import tempfile

ANY_SIZE_FILENAME = "model.any_size.onnx"


def make_any_size(src: str, dst: str) -> int:
    """Writes a copy of the model at `src` with its layer norms' baked-in
    tile size replaced by the input's own. Returns how many constants were
    replaced."""
    import onnx
    from onnx import helper

    model = onnx.load(src)
    graph = model.graph
    initializers = {init.name for init in graph.initializer}
    nodes = list(graph.node)
    producer = {output: node for node in nodes for output in node.output}

    def shape_of(mul) -> str:
        # mul_1 = N * H, with N squeezed out of a slice of the int [N, H, W, C]
        # shape; mul_2 = mul_1 * W. tf2onnx merged identical subgraphs, so the
        # shape may belong to another layer of the same size.
        first = producer[mul.input[0]]
        if first.op_type == "Mul":
            first = producer[first.input[0]]
        sliced = producer[first.input[0]]
        if first.op_type != "Squeeze" or sliced.op_type != "Slice":
            raise ValueError(f"unexpected layer norm structure at {mul.name}")
        return sliced.input[0]

    patched = []
    replaced = 0
    for node in nodes:
        leaf = node.name.rpartition("/")[2]
        if (
            node.op_type == "Mul"
            and "layer_normalization" in node.name
            and leaf in ("mul_1", "mul_2")
            and node.input[1] in initializers
        ):
            axis = 1 if leaf == "mul_1" else 2
            sliced, dim = f"{node.name}/size_slice", f"{node.name}/size"
            patched.append(
                helper.make_node("Slice", [shape_of(node)], [sliced], name=sliced, axes=[0], starts=[axis], ends=[axis + 1])
            )
            patched.append(helper.make_node("Squeeze", [sliced], [dim], name=dim, axes=[0]))
            node.input[1] = dim
            replaced += 1
        patched.append(node)

    del graph.node[:]
    graph.node.extend(patched)
    for value in list(graph.input) + list(graph.output):
        dims = value.type.tensor_type.shape.dim
        dims[0].dim_param, dims[1].dim_param, dims[2].dim_param = "batch", "height", "width"
    del graph.value_info[:]  # the old fixed intermediate shapes
    onnx.checker.check_model(model)

    # Written to a temp file and moved into place, so a worker that dies (or
    # a second one doing the same) never leaves a half-written model behind.
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dst), suffix=".onnx.tmp")
    os.close(fd)
    try:
        onnx.save(model, tmp)
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return replaced


def any_size_path(checkpoint_dir: str) -> str:
    """The any-size copy of the model in `checkpoint_dir`, made on first use."""
    path = os.path.join(checkpoint_dir, ANY_SIZE_FILENAME)
    if not os.path.exists(path):
        make_any_size(os.path.join(checkpoint_dir, "model.onnx"), path)
    return path
