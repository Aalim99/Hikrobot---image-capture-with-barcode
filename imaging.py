"""Pure geometry helpers for preview rendering (no GUI imports, so testable)."""


def fit_letterbox(src_w: int, src_h: int, box_w: int, box_h: int):
    """Fit a src_w x src_h image into a box without distorting it.

    Returns (draw_w, draw_h, offset_x, offset_y, scale). The same scale and
    offsets map barcode coordinates from frame space onto the preview.
    """
    if min(src_w, src_h, box_w, box_h) <= 0:
        return 0, 0, 0, 0, 1.0

    scale = min(box_w / src_w, box_h / src_h)
    draw_w = max(1, int(round(src_w * scale)))
    draw_h = max(1, int(round(src_h * scale)))
    return draw_w, draw_h, (box_w - draw_w) // 2, (box_h - draw_h) // 2, scale
