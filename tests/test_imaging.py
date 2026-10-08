from imaging import fit_letterbox


def test_exact_fit_has_no_offset():
    w, h, ox, oy, scale = fit_letterbox(1920, 1080, 960, 540)
    assert (w, h, ox, oy) == (960, 540, 0, 0)
    assert scale == 0.5


def test_wide_box_letterboxes_horizontally():
    w, h, ox, oy, _ = fit_letterbox(1000, 1000, 800, 400)
    assert (w, h) == (400, 400)
    assert (ox, oy) == (200, 0)


def test_tall_box_letterboxes_vertically():
    w, h, ox, oy, _ = fit_letterbox(1000, 500, 400, 400)
    assert (w, h) == (400, 200)
    assert (ox, oy) == (0, 100)


def test_degenerate_sizes_are_safe():
    assert fit_letterbox(0, 100, 200, 200) == (0, 0, 0, 0, 1.0)
    assert fit_letterbox(100, 100, 0, 200) == (0, 0, 0, 0, 1.0)
