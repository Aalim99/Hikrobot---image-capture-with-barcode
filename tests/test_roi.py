import pytest

import roi as roi_mod
from roi import Roi


def test_from_points_orders_corners_and_clamps():
    box = roi_mod.from_points((0.6, 0.8), (0.2, 0.1))
    assert (box.x, box.y) == pytest.approx((0.2, 0.1))
    assert (box.w, box.h) == pytest.approx((0.4, 0.7))

    outside = roi_mod.from_points((-0.5, -0.5), (1.5, 1.5))
    assert (outside.x, outside.y, outside.w, outside.h) == (0.0, 0.0, 1.0, 1.0)


def test_from_points_never_collapses_to_nothing():
    box = roi_mod.from_points((0.5, 0.5), (0.5, 0.5))
    assert box.w >= roi_mod.MIN_SIZE and box.h >= roi_mod.MIN_SIZE


def test_clamp_keeps_box_inside_frame():
    box = roi_mod.clamp(Roi(0.9, 0.95, 0.5, 0.5))
    assert box.right <= 1.0 + 1e-9 and box.bottom <= 1.0 + 1e-9
    assert box.w == pytest.approx(0.5) and box.h == pytest.approx(0.5)


def test_list_roundtrip_and_bad_values():
    box = Roi(0.1, 0.2, 0.3, 0.4)
    assert Roi.from_list(box.to_list()) == box
    assert Roi.from_list(None) is None
    assert Roi.from_list("nonsense") is None
    assert Roi.from_list([0.1, 0.2]) is None
    assert Roi.from_list([0.1, 0.2, 0.0, 0.4]) is None


def test_to_pixels_is_even_aligned_and_inside_frame():
    left, top, w, h = roi_mod.to_pixels(Roi(0.1234, 0.2345, 0.3, 0.4), 5472, 3648)
    assert left % 2 == 0 and top % 2 == 0 and w % 2 == 0 and h % 2 == 0
    assert left + w <= 5472 and top + h <= 3648
    assert abs(left - 0.1234 * 5472) <= 2


def test_to_pixels_full_frame_and_tiny_box():
    assert roi_mod.to_pixels(Roi(0, 0, 1, 1), 1000, 800) == (0, 0, 1000, 800)
    left, top, w, h = roi_mod.to_pixels(Roi(0.999, 0.999, 0.02, 0.02), 1000, 800)
    assert w >= 2 and h >= 2 and left + w <= 1000 and top + h <= 800


BOX = Roi(0.3, 0.3, 0.4, 0.4)
TOL = 0.02


@pytest.mark.parametrize("pt,expected", [
    ((0.3, 0.3), "nw"), ((0.7, 0.3), "ne"), ((0.7, 0.7), "se"), ((0.3, 0.7), "sw"),
    ((0.5, 0.3), "n"), ((0.5, 0.7), "s"), ((0.3, 0.5), "w"), ((0.7, 0.5), "e"),
    ((0.5, 0.5), "move"), ((0.1, 0.1), None), ((0.9, 0.5), None),
])
def test_hit_test(pt, expected):
    assert roi_mod.hit_test(BOX, pt, TOL, TOL) == expected


def test_drag_move_slides_and_stops_at_frame_edge():
    moved = roi_mod.drag("move", BOX, (0.5, 0.5), (0.6, 0.45))
    assert (moved.x, moved.y) == pytest.approx((0.4, 0.25))
    assert (moved.w, moved.h) == pytest.approx((0.4, 0.4))

    stuck = roi_mod.drag("move", BOX, (0.5, 0.5), (5.0, 5.0))
    assert stuck.right == pytest.approx(1.0) and stuck.bottom == pytest.approx(1.0)
    assert (stuck.w, stuck.h) == pytest.approx((0.4, 0.4))


def test_drag_handles_resize_only_their_edges():
    east = roi_mod.drag("e", BOX, (0.7, 0.5), (0.8, 0.9))
    assert east.x == pytest.approx(0.3) and east.right == pytest.approx(0.8)
    assert east.y == pytest.approx(0.3) and east.bottom == pytest.approx(0.7)

    nw = roi_mod.drag("nw", BOX, (0.3, 0.3), (0.2, 0.1))
    assert (nw.x, nw.y) == pytest.approx((0.2, 0.1))
    assert (nw.right, nw.bottom) == pytest.approx((0.7, 0.7))


def test_drag_edge_past_opposite_edge_flips_instead_of_inverting():
    flipped = roi_mod.drag("w", BOX, (0.3, 0.5), (0.9, 0.5))
    assert flipped.x == pytest.approx(0.7) and flipped.right == pytest.approx(0.9)
    assert flipped.w > 0
