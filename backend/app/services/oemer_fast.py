"""Faster copies of six of oemer's rule-based functions, same results.

Once the neural nets were sped up (oemer_inference), most of what was left
of a page's time -- ~45s of ~55s on one test page -- was six functions with
one flaw in common: for every note, group, stem or beam they compare the
whole 3.7-megapixel label map to its id (`np.where(label_map == i)`) to find
pixels that all lie in one small box. Here each map's boxes come from one
`scipy.ndimage.find_objects` pass, and the same comparison is made inside
the box only -- the pixels found, and their order, are identical.

Everything else is oemer 0.1.8's code line for line, except two debug
images (`grp_img`, `ratio_map`) that nothing reads, which aren't drawn.
oemer_engine swaps these in for the duration of a page, and the output was
checked byte for byte against oemer's own on four test pages.
"""

from typing import Any

import cv2
import numpy as np
import scipy.ndimage
from oemer import layers
from oemer.bbox import get_rotated_bbox
from oemer.note_group_extraction import NoteGroup, check_valid_new_group, get_possible_nearby_gid
from oemer.rhythm_extraction import polish_symbols
from oemer.utils import find_closest_staffs, get_unit_size


def label_boxes(label_map: np.ndarray, offset: int = 0) -> list:
    """The bounding slices of every value in an integer label map, indexed by
    value (+ `offset`, for maps that use -1 for "none")."""
    return [None] + scipy.ndimage.find_objects(label_map + offset if offset else label_map)


def where_in_box(label_map: np.ndarray, value, box) -> tuple[np.ndarray, np.ndarray]:
    """`np.where(label_map == value)`, searching only `box` -- which must hold
    every pixel of that value."""
    if box is None:
        empty = np.array([], dtype=np.int64)
        return empty, empty
    ys, xs = np.where(label_map[box] == value)
    return ys + box[0].start, xs + box[1].start


def _box_of(boxes, value):
    return boxes[value] if 0 <= value < len(boxes) else None


def parse_stem_info(notes) -> None:
    # notehead_extraction.parse_stem_info
    stems = layers.get_layer("stems_rests_pred")

    ker = np.ones((3, 2), dtype=np.uint8)
    enhanced_stems = cv2.dilate(stems.astype(np.uint8), ker)
    st_map, _ = scipy.ndimage.label(enhanced_stems)
    boxes = label_boxes(st_map)

    for note in notes:
        box = note.bbox
        region = st_map[box[1] : box[3], box[0] : box[2]]
        lls = set(np.unique(region))
        if 0 in lls:
            lls.remove(0)
        if len(lls) == 0:
            continue

        label = lls.pop()
        _, xi = where_in_box(st_map, label, _box_of(boxes, label))
        st_cen_x = np.mean(xi)
        cen_x = (box[0] + box[2]) / 2
        on_right = st_cen_x > cen_x
        note.stem_right = bool(on_right)


def group_noteheads() -> tuple[dict[int, list[int]], np.ndarray]:
    # note_group_extraction.group_noteheads
    note_id_map = layers.get_layer("note_id")
    notehead = layers.get_layer("notehead_pred")
    stems = layers.get_layer("stems_rests_pred")

    ker = np.ones((3, 2), dtype=np.uint8)
    ext_stems = cv2.dilate(stems.astype(np.uint8), ker)

    mix = notehead + ext_stems
    mix[mix > 1] = 1
    nh_label, _ = scipy.ndimage.label(mix)
    nids = set(np.unique(note_id_map))
    if -1 in nids:
        nids.remove(-1)
    note_boxes = label_boxes(note_id_map, offset=1)

    groups: dict = {}
    for nid in nids:
        nys, nxs = where_in_box(note_id_map, nid, _box_of(note_boxes, nid + 1))

        offset = 3
        top = np.min(nys) - offset
        bt = np.max(nys) + offset
        left = np.min(nxs)
        right = np.max(nxs)
        covered_region = nh_label[top:bt, left:right]
        labels = set(np.unique(covered_region))
        if 0 in labels:
            labels.remove(0)

        if len(labels) == 0:
            continue
        elif len(labels) > 1:
            keys = set(groups.keys())
            inter = labels.intersection(keys)
            if len(inter) == 0:
                label = labels.pop()
            elif len(inter) == 1:
                label = inter.pop()
            else:
                label = inter.pop()
                tmp_g = groups[label]
                for k in inter:
                    tmp_g.extend(groups[k])
                    del groups[k]
                groups[label] = tmp_g

            for ll in labels:
                nh_label[nh_label == ll] = label
        else:
            label = labels.pop()

        if label not in groups:
            groups[label] = []
        groups[label].append(nid)

    # Remove the groups that have no noteheads attached -- in one lookup
    # rather than a pass over the map per label.
    lls = set(np.unique(nh_label))
    diff = lls.difference(groups.keys())
    if diff:
        keep = np.arange(nh_label.max() + 1, dtype=nh_label.dtype)
        keep[list(diff)] = 0
        nh_label = keep[nh_label]

    return groups, nh_label


def parse_stem_direction(
    groups: dict[int, list[int]],
    group_map: np.ndarray,
    tolerance_ratio: float = 0.2,
    max_x_diff_ratio: float = 0.5,
) -> tuple[dict[int, list[int]], np.ndarray]:
    # note_group_extraction.parse_stem_direction
    notes = layers.get_layer("notes")
    boxes = label_boxes(group_map, offset=1)

    temp_result = {}
    for gp, nids in groups.items():
        gy, gx = where_in_box(group_map, gp, _box_of(boxes, gp + 1))
        gbox = (np.min(gx), np.min(gy), np.max(gx), np.max(gy))
        nbox = np.array([notes[nid].bbox for nid in nids])
        nbox = (np.min(nbox[:, 0]), np.min(nbox[:, 1]), np.max(nbox[:, 2]), np.max(nbox[:, 3]))
        nh = np.mean([notes[nid].bbox[3] - notes[nid].bbox[1] for nid in nids])
        tolerance = nh * tolerance_ratio

        gp_higher = gbox[1] < nbox[1] - tolerance
        gp_lower = gbox[3] > nbox[3] + tolerance

        if gp_higher and not gp_lower:
            temp_result[gp] = True
            for nid in nids:
                notes[nid].stem_up = True
            continue
        elif not gp_higher and gp_lower:
            temp_result[gp] = False
            for nid in nids:
                notes[nid].stem_up = False
            continue

        if len(nids) == 1:
            nid = nids[0]
            new_group = get_possible_nearby_gid(notes[nid], group_map)
            if (new_group is not None) and check_valid_new_group(gp, new_group, group_map, max_x_diff_ratio):
                if new_group in temp_result:
                    notes[nid].stem_up = temp_result[new_group]

                group_map = np.where(group_map == gp, new_group, group_map)
                boxes = label_boxes(group_map, offset=1)  # the map changed
                groups[new_group].append(nid)
                old_gp_nidx = groups[gp].index(nid)
                del groups[gp][old_gp_nidx]

    groups = {gp: nids for gp, nids in groups.items() if len(nids) > 0}
    return groups, group_map


def gen_groups(groups: dict[int, list[int]], group_map: np.ndarray) -> tuple[list, np.ndarray]:
    # note_group_extraction.gen_groups, without drawing the debug grp_img
    notes = layers.get_layer("notes")
    boxes = label_boxes(group_map, offset=1)

    ngs = []
    new_map = np.zeros_like(group_map) - 1
    idx = 0
    for gid, nids in groups.items():
        ng = NoteGroup()
        ng.id = idx
        ng.note_ids = nids
        box = _box_of(boxes, gid + 1)
        gy, gx = where_in_box(group_map, gid, box)
        gbox = (int(np.min(gx)), int(np.min(gy)), int(np.max(gx)), int(np.max(gy)))
        nbox = np.array([notes[nid].bbox for nid in nids])
        nbox = (np.min(nbox[:, 0]), np.min(nbox[:, 1]), np.max(nbox[:, 2]), np.max(nbox[:, 3]))

        ng.bbox = gbox
        for nid in nids:
            notes[nid].note_group_id = idx

        if notes[nids[0]].stem_up is None:
            nh = np.mean([notes[nid].bbox[3] - notes[nid].bbox[1] for nid in nids])
            g_height = gbox[3] - gbox[1]
            n_height = nbox[3] - nbox[1]
            if abs(g_height - n_height) > nh // 5:
                ng.has_stem = True
            else:
                ng.has_stem = False
        elif notes[nids[0]].stem_up:
            ng.stem_up = True
            ng.has_stem = True
        else:
            ng.stem_up = False
            ng.has_stem = True

        n_types = [notes[nid].label for nid in nids]
        ng.all_same_type = all(nt == n_types[0] for nt in n_types)

        tar_track = notes[nids[0]].track
        tar_group = notes[nids[0]].group
        same_track = all(notes[nid].track == tar_track for nid in nids)
        same_group = all(notes[nid].group == tar_group for nid in nids)
        if not (same_track and same_group):
            y_mass_center = (gbox[1] + gbox[3]) / 2
            x_mass_center = (gbox[0] + gbox[2]) / 2
            st, _ = find_closest_staffs(x_mass_center, y_mass_center)
            tar_track = st.track
            tar_group = st.group
            for nid in nids:
                notes[nid].track = st.track
                notes[nid].group = st.group

        ng.track = tar_track
        ng.group = tar_group

        new_map[box][group_map[box] == gid] = idx
        ngs.append(ng)
        idx += 1
    return ngs, new_map


def parse_beams(min_area_ratio: float = 0.07, min_tp_ratio: float = 0.4, min_width_ratio: float = 0.2):
    # rhythm_extraction.parse_beams, without drawing the debug ratio_map.
    # oemer finds each box's pixels by filling it into a page-sized index map
    # and searching the whole map; filling it into a window just its size
    # gives the same pixels (OpenCV's fill is the same under a whole-pixel
    # shift).
    symbols = layers.get_layer("symbols_pred")
    staff_pred = layers.get_layer("staff_pred")
    stems = layers.get_layer("stems_rests_pred")
    group_map = layers.get_layer("group_map")
    clefs_sfns = layers.get_layer("clefs_keys_pred")

    beams = polish_symbols(staff_pred, symbols, stems, clefs_sfns, group_map)
    beams = beams - np.where(group_map > -1, 1, 0) - stems
    beams[beams < 0] = 0

    rboxes = get_rotated_bbox(beams)
    poly_map = np.ones(symbols.shape + (3,), dtype=np.uint8) * 255
    idx = np.where(beams > 0)
    poly_map[idx[0], idx[1]] = 0
    invalid_map = np.zeros_like(poly_map)
    height, width = symbols.shape

    null_color = (255, 255, 255)
    valid_box: list[Any] = []
    valid_idxs = []
    for rbox in rboxes:
        cnt = cv2.boxPoints(rbox)
        if any(cc < 0 for cc in cnt.reshape(-1, 1).squeeze()):
            continue
        cnt = cnt.astype(np.uint64)
        centers = np.sum(cnt, axis=0) / 4
        unit_size = get_unit_size(round(centers[0]), round(centers[1]))

        area = cv2.contourArea(cnt)
        min_area = unit_size**2 * min_area_ratio
        if area < min_area:
            cv2.fillPoly(poly_map, [cnt], color=null_color)
            cv2.fillPoly(invalid_map, [cnt], color=null_color)
            continue

        points = cnt.astype(np.int64)
        x0, y0 = points.min(axis=0)
        x1 = min(int(points[:, 0].max()) + 1, width)
        y1 = min(int(points[:, 1].max()) + 1, height)
        window = np.zeros((max(0, y1 - y0), max(0, x1 - x0)), dtype=np.uint8)
        if window.size:
            cv2.fillPoly(window, [(points - (x0, y0)).astype(np.int32)], color=1)
        yi, xi = np.where(window == 1)  # nothing, for a box off the page
        yi, xi = yi + y0, xi + x0
        pts = beams[yi, xi]
        meta_idx = np.where(pts > 0)[0]
        ryi = yi[meta_idx]
        rxi = xi[meta_idx]

        r_width = min(rbox[1])
        if r_width < unit_size * min_width_ratio:
            poly_map[ryi, rxi] = np.array(null_color)
            invalid_map[ryi, rxi] = np.array(null_color)
            continue

        ratio = len(meta_idx) / (len(yi) + 1e-8)
        if ratio < min_tp_ratio:
            poly_map[ryi, rxi] = np.array(null_color)
            invalid_map[ryi, rxi] = np.array(null_color)
            continue

        valid_idxs.append((ryi, rxi))
        valid_box.append(rbox)

    for ryi, rxi in valid_idxs:
        poly_map[ryi, rxi] = 255 - np.array(null_color)

    poly_map = np.where(np.sum(poly_map, axis=-1) < 1, 1, 0)
    invalid_map = invalid_map[..., 0] / 255
    return poly_map, valid_box, invalid_map


def parse_beam_overlap_regions(poly_map: np.ndarray, invalid_map: np.ndarray):
    # rhythm_extraction.parse_beam_overlap_regions
    symbols = layers.get_layer("symbols_pred")
    group_map = layers.get_layer("group_map")
    barlines = layers.get_layer("barlines")

    mix = poly_map + symbols
    mix[mix < 0] = 0
    ker = np.ones((3, 3), dtype=np.uint8)
    mix = cv2.dilate(cv2.erode(mix.astype(np.uint8), ker), ker)
    poly_map = cv2.dilate(cv2.erode(poly_map.astype(np.uint8), ker), ker)

    for bl in barlines:
        box = bl.bbox
        mix[box[1] : box[3], box[0] : box[2]] = 0
        poly_map[box[1] : box[3], box[0] : box[2]] = 0

    mix[mix > 1] = 1
    reg_map, feat_num = scipy.ndimage.label(poly_map)
    sym_map, _ = scipy.ndimage.label(mix)
    reg_boxes = label_boxes(reg_map)
    sym_boxes = label_boxes(sym_map)

    out_map = np.zeros_like(reg_map)
    map_info: dict = {}
    for idx in range(1, feat_num + 1):
        reg_box = _box_of(reg_boxes, idx)
        if reg_box is None:
            reg_box = (slice(0, 0), slice(0, 0))
        mask = reg_map[reg_box] == idx
        sym_labels = set(np.unique(sym_map[reg_box][mask]))
        if 0 in sym_labels:
            sym_labels.remove(0)

        yi, xi = [], []
        for label in sym_labels:
            yy, xx = where_in_box(sym_map, label, _box_of(sym_boxes, label))
            yi.extend(list(yy))
            xi.extend(list(xx))

        g_overlap = group_map[np.array(yi).astype(int), np.array(xi).astype(int)]
        gids = set(np.unique(g_overlap))
        if -1 in gids:
            gids.remove(-1)
        if len(gids) == 0:
            continue

        out_map[reg_box][mask] = 1
        box = (np.min(xi), np.min(yi), np.max(xi), np.max(yi))
        for sym_label in sym_labels:
            if sym_label in map_info:
                bb = map_info[sym_label]["bbox"]
                gids.update(map_info[sym_label]["gids"])
                box = (min(bb[0], box[0]), min(bb[1], box[1]), max(bb[2], box[2]), max(bb[3], box[3]))
            map_info[sym_label] = {"bbox": box, "gids": gids}

    ker = np.ones((3, 3), dtype=np.uint8)
    out_map = cv2.erode(cv2.dilate(out_map.astype(np.uint8), ker), ker)
    return out_map, map_info


# (module, name) -> replacement, for oemer_engine to swap in.
REPLACEMENTS = {
    ("oemer.notehead_extraction", "parse_stem_info"): parse_stem_info,
    ("oemer.note_group_extraction", "group_noteheads"): group_noteheads,
    ("oemer.note_group_extraction", "parse_stem_direction"): parse_stem_direction,
    ("oemer.note_group_extraction", "gen_groups"): gen_groups,
    ("oemer.rhythm_extraction", "parse_beams"): parse_beams,
    ("oemer.rhythm_extraction", "parse_beam_overlap_regions"): parse_beam_overlap_regions,
}
