"""Scan-area (ROI) geometry in normalised image space.

The operator draws a box on the live view to say where the barcode is. The
box is stored as fractions of the frame (0..1), so it keeps pointing at the
same spot when the camera resolution changes, and so the editing maths here
needs no knowledge of widget sizes. Pure functions only - no Qt imports -
so it is unit-testable.
"""
from dataclasses import dataclass

MIN_SIZE = 0.02  # smallest box edge, as a fraction of the frame

HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")


@dataclass(frozen=True)
class Roi:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    def to_list(self) -> list:
        return [self.x, self.y, self.w, self.h]

    @classmethod
    def from_list(cls, values):
        """Parse a settings.json value; anything malformed means 'no ROI'."""
        try:
            x, y, w, h = (float(v) for v in values)
        except (TypeError, ValueError):
            return None
        if w <= 0 or h <= 0:
            return None
        return clamp(cls(x, y, w, h))


def clamp(roi: Roi) -> Roi:
    """Force a box inside the frame and at least MIN_SIZE on each edge."""
    w = min(max(roi.w, MIN_SIZE), 1.0)
    h = min(max(roi.h, MIN_SIZE), 1.0)
    x = min(max(roi.x, 0.0), 1.0 - w)
    y = min(max(roi.y, 0.0), 1.0 - h)
    return Roi(x, y, w, h)


def from_points(p0, p1) -> Roi:
    """Box spanned by two corner points, in any order (a rubber-band drag)."""
    x0, x1 = sorted((p0[0], p1[0]))
    y0, y1 = sorted((p0[1], p1[1]))
    x0, x1 = min(max(x0, 0.0), 1.0), min(max(x1, 0.0), 1.0)
    y0, y1 = min(max(y0, 0.0), 1.0), min(max(y1, 0.0), 1.0)
    return clamp(Roi(x0, y0, x1 - x0, y1 - y0))


def to_pixels(roi: Roi, frame_w: int, frame_h: int, align: int = 2):
    """(left, top, width, height) in frame pixels.

    Edges are snapped to multiples of `align` (2 by default) so cropping a
    raw Bayer mosaic keeps its colour phase. Always returns a non-empty box
    inside the frame.
    """
    def snap(value):
        return int(round(value / align)) * align

    left = max(0, snap(roi.x * frame_w))
    top = max(0, snap(roi.y * frame_h))
    right = min(frame_w, snap(roi.right * frame_w))
    bottom = min(frame_h, snap(roi.bottom * frame_h))

    # keep at least one aligned block, even for a tiny box on the frame edge
    if right - left < align:
        left = max(0, min(left, frame_w - align))
        right = left + align
    if bottom - top < align:
        top = max(0, min(top, frame_h - align))
        bottom = top + align
    return left, top, right - left, bottom - top


def hit_test(roi: Roi, pt, tol_x: float, tol_y: float):
    """What is under `pt`? A handle name, "move" (inside the box) or None.

    tol_x / tol_y are the grab tolerance in normalised units (the caller
    converts a few screen pixels, which differ per axis once normalised).
    """
    px, py = pt
    near_l = abs(px - roi.x) <= tol_x
    near_r = abs(px - roi.right) <= tol_x
    near_t = abs(py - roi.y) <= tol_y
    near_b = abs(py - roi.bottom) <= tol_y
    in_x = roi.x - tol_x <= px <= roi.right + tol_x
    in_y = roi.y - tol_y <= py <= roi.bottom + tol_y

    if near_t and near_l:
        return "nw"
    if near_t and near_r:
        return "ne"
    if near_b and near_r:
        return "se"
    if near_b and near_l:
        return "sw"
    if near_t and in_x:
        return "n"
    if near_b and in_x:
        return "s"
    if near_l and in_y:
        return "w"
    if near_r and in_y:
        return "e"
    if roi.x < px < roi.right and roi.y < py < roi.bottom:
        return "move"
    return None


def drag(mode: str, start: Roi, p0, p1) -> Roi:
    """The box after dragging from p0 to p1 in `mode` (a hit_test result).

    "move" slides the whole box (it stops at the frame edge instead of
    shrinking). A handle moves its edge(s); dragging an edge past the
    opposite one flips the box rather than inverting it.
    """
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]

    if mode == "move":
        x = min(max(start.x + dx, 0.0), 1.0 - start.w)
        y = min(max(start.y + dy, 0.0), 1.0 - start.h)
        return Roi(x, y, start.w, start.h)

    left, top, right, bottom = start.x, start.y, start.right, start.bottom
    if "w" in mode:
        left += dx
    if "e" in mode:
        right += dx
    if "n" in mode:
        top += dy
    if "s" in mode:
        bottom += dy
    return from_points((left, top), (right, bottom))
