"""VideoView edit interaction, driven with real (synthetic) mouse events."""
import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from roi import Roi
from ui.video_view import VideoView


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def view(app):
    widget = VideoView()
    widget.resize(800, 600)
    # a 1600x1200 frame (4:3) fills the 800x600 widget exactly: 1 screen px = 2 frame px
    widget.show_frame(np.zeros((300, 400, 3), np.uint8), 1600, 1200)
    widget.show()
    yield widget
    widget.close()


def at(x, y):
    """Screen point for normalised image coordinates (the image fills the widget)."""
    return QPoint(int(x * 800), int(y * 600))


def drag(view, start, end, steps=6):
    QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, start)
    for i in range(1, steps + 1):
        point = QPoint(start.x() + (end.x() - start.x()) * i // steps,
                       start.y() + (end.y() - start.y()) * i // steps)
        QTest.mouseMove(view, point)
    QTest.mouseRelease(view, Qt.LeftButton, Qt.NoModifier, end)


def test_draw_a_new_box(view):
    view.begin_edit()
    drag(view, at(0.2, 0.3), at(0.6, 0.5))

    box = view.working_roi
    assert (box.x, box.y, box.w, box.h) == pytest.approx((0.2, 0.3, 0.4, 0.2), abs=0.01)


def test_draw_in_any_direction(view):
    view.begin_edit()
    drag(view, at(0.7, 0.8), at(0.3, 0.4))
    box = view.working_roi
    assert (box.x, box.y, box.right, box.bottom) == pytest.approx((0.3, 0.4, 0.7, 0.8), abs=0.01)


def test_drag_inside_moves_the_box(view):
    view.set_roi(Roi(0.2, 0.2, 0.3, 0.3))
    view.begin_edit()
    drag(view, at(0.35, 0.35), at(0.55, 0.50))   # inside the box, away from edges

    box = view.working_roi
    assert (box.x, box.y) == pytest.approx((0.4, 0.35), abs=0.01)
    assert (box.w, box.h) == pytest.approx((0.3, 0.3), abs=0.01)


def test_drag_a_corner_handle_resizes(view):
    view.set_roi(Roi(0.2, 0.2, 0.3, 0.3))
    view.begin_edit()
    drag(view, at(0.5, 0.5), at(0.7, 0.8))        # the south-east corner

    box = view.working_roi
    assert (box.x, box.y) == pytest.approx((0.2, 0.2), abs=0.01)
    assert (box.right, box.bottom) == pytest.approx((0.7, 0.8), abs=0.01)


def test_drag_an_edge_handle_only_moves_that_edge(view):
    view.set_roi(Roi(0.2, 0.2, 0.3, 0.3))
    view.begin_edit()
    drag(view, at(0.35, 0.2), at(0.35, 0.05))     # the north edge, mid-way along it

    box = view.working_roi
    assert box.y == pytest.approx(0.05, abs=0.01)
    assert (box.x, box.right, box.bottom) == pytest.approx((0.2, 0.5, 0.5), abs=0.01)


def test_dragging_outside_the_image_is_clamped(view):
    view.begin_edit()
    drag(view, at(0.5, 0.5), QPoint(2000, 2000))
    box = view.working_roi
    assert box.right == pytest.approx(1.0) and box.bottom == pytest.approx(1.0)


def test_a_plain_click_does_not_replace_the_box(view):
    view.set_roi(Roi(0.2, 0.2, 0.3, 0.3))
    view.begin_edit()
    drag(view, at(0.8, 0.8), at(0.8, 0.8), steps=1)
    assert view.working_roi == Roi(0.2, 0.2, 0.3, 0.3)


def test_cancel_restores_the_previous_box(view):
    original = Roi(0.2, 0.2, 0.3, 0.3)
    view.set_roi(original)
    view.begin_edit()
    drag(view, at(0.7, 0.7), at(0.9, 0.9))
    assert view.working_roi != original

    assert view.end_edit(commit=False) == original
    assert not view.editing and view.working_roi is None


def test_commit_keeps_the_new_box(view):
    view.begin_edit()
    drag(view, at(0.1, 0.1), at(0.4, 0.3))
    committed = view.end_edit(commit=True)
    assert committed.x == pytest.approx(0.1, abs=0.01)
    assert not view.editing


def test_clear_removes_the_box(view):
    view.set_roi(Roi(0.2, 0.2, 0.3, 0.3))
    view.begin_edit()
    view.clear_working_roi()
    assert view.end_edit(commit=True) is None


def test_mouse_is_ignored_when_not_editing(view):
    drag(view, at(0.2, 0.2), at(0.6, 0.6))
    assert view.working_roi is None and view._roi is None


def test_enter_and_escape_emit_edit_finished(view):
    seen = []
    view.editFinished.connect(seen.append)
    view.begin_edit()
    QTest.keyClick(view, Qt.Key_Return)
    QTest.keyClick(view, Qt.Key_Escape)
    assert seen == [True, False]


def test_edit_changed_fires_after_a_drag(view):
    count = []
    view.editChanged.connect(lambda: count.append(1))
    view.begin_edit()
    drag(view, at(0.1, 0.1), at(0.5, 0.5))
    assert count == [1]


def test_mapping_respects_letterboxing(app):
    widget = VideoView()
    widget.resize(1000, 400)                       # wide widget, 4:3 image -> bars left and right
    widget.show_frame(np.zeros((30, 40, 3), np.uint8), 400, 300)
    rect = widget._image_rect()
    assert rect.width() == pytest.approx(400 * 4 / 3, abs=2) and rect.height() == pytest.approx(400, abs=1)
    assert widget._to_norm(rect.center()) == pytest.approx((0.5, 0.5), abs=0.01)
    assert widget._to_norm(QPoint(0, 200)) == pytest.approx((0.0, 0.5), abs=0.01)   # in the bar: clamped
    widget.close()
    widget.deleteLater()


def test_paints_every_state_without_error(view, tmp_path):
    from barcode_scanner import Detection

    view.set_overlays([Detection("SN-1", (100, 100, 300, 200), "QRCode")], None)
    view.set_roi(Roi(0.1, 0.1, 0.5, 0.5))
    assert not view.grab().isNull()
    view.begin_edit()
    assert not view.grab().isNull()
    view.end_edit(False)
    view.set_overlays([], (200, 200, 400, 100))
    view.set_roi(None)
    assert not view.grab().isNull()
    view.show_offline("NO SIGNAL", "camera not connected")
    assert not view.grab().isNull()
