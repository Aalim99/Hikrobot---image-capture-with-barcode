"""The real MainWindow driven end to end with the demo camera: worker thread,
barcode scan, state machine, async save, scan-area editing with real mouse
events. Slower than the unit tests (a couple of seconds each)."""
import time

import cv2
import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt, qInstallMessageHandler
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import config
from demo_source import DemoCamera
from roi import Roi
from ui.main_window import MainWindow
from ui.theme import STYLESHEET

ITEM2_BOX = Roi(0.12, 0.45, 0.34, 0.35)      # around SN-DEMO-0002's label (lower left)
EMPTY_CORNER = Roi(0.70, 0.70, 0.20, 0.20)   # nothing there


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    return app


class Rig:
    """A window plus the knobs the tests turn."""

    def __init__(self, win, camera, tmp_path, boxes):
        self.win, self.camera, self.tmp_path, self.boxes = win, camera, tmp_path, boxes

    def show(self, serial):
        self.camera._current_serial = lambda: serial

    def arm(self):
        """Press START (the rig begins stopped so tests can set the scan area first)."""
        if not self.win.engine.running:
            self.win.start_btn.click()

    @property
    def captures(self):
        return self.tmp_path / "captures"

    def files(self, pattern="*.jpg"):
        return sorted(self.captures.rglob(pattern)) if self.captures.exists() else []

    def pos(self, x, y):
        rect = self.win.view._image_rect()
        return QPoint(int(rect.left() + x * rect.width()), int(rect.top() + y * rect.height()))

    def drag(self, start, end, steps=6):
        view = self.win.view
        QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, start)
        for i in range(1, steps + 1):
            QTest.mouseMove(view, QPoint(start.x() + (end.x() - start.x()) * i // steps,
                                         start.y() + (end.y() - start.y()) * i // steps))
        QTest.mouseRelease(view, Qt.LeftButton, Qt.NoModifier, end)


def wait_until(predicate, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        QTest.qWait(25)
    return predicate()


@pytest.fixture
def rig(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.json")
    boxes = []
    for kind in ("critical", "warning", "information"):
        monkeypatch.setattr(QMessageBox, kind, lambda *a, _k=kind, **kw: boxes.append((_k, a)))

    settings = config.load_settings()
    settings.update(output_dir=str(tmp_path / "captures"), capture_delay_seconds=0.4,
                    barcode_lost_reset_seconds=0.6)
    camera = DemoCamera(1368, 912)
    camera._current_serial = lambda: "SN-DEMO-0002"
    win = MainWindow(camera, settings, demo=True)
    win.engine.stop()                    # nothing may be captured before a test is ready
    win._refresh_buttons()
    win.resize(1360, 880)
    win.show()
    assert wait_until(lambda: win._have_frame), "the live view never received a frame"
    yield Rig(win, camera, tmp_path, boxes)
    win.close()


def test_stylesheet_has_no_parse_errors(qapp):
    messages = []
    previous = qInstallMessageHandler(lambda _t, _c, msg: messages.append(msg))
    try:
        qapp.setStyleSheet("")
        qapp.setStyleSheet(STYLESHEET)
    finally:
        qInstallMessageHandler(previous)
    assert [m for m in messages if "stylesheet" in m.lower() or "parse" in m.lower()] == []


def test_barcode_in_scan_area_is_captured_into_its_own_folder(rig):
    rig.win._apply_scan_area(ITEM2_BOX)
    rig.arm()
    assert wait_until(lambda: rig.win.session_count == 1)

    jpg = rig.captures / "SN-DEMO-0002" / "SN-DEMO-0002_attempt_1.jpg"
    assert jpg.exists()
    image = cv2.imdecode(np.fromfile(str(jpg), np.uint8), cv2.IMREAD_COLOR)
    assert image.shape[:2] == (912, 1368), "the saved image is the full frame, not the crop or the preview"

    log = (jpg.parent / "log.txt").read_text(encoding="utf-8")
    assert "Barcode: SN-DEMO-0002" in log and "x 12.0%" in log and "automatic" in log
    assert rig.win.history[0][1:] == ("SN-DEMO-0002", 1)
    assert rig.win.count_label.text() == "1"


def test_item_stays_in_view_is_captured_only_once(rig):
    rig.win._apply_scan_area(ITEM2_BOX)
    rig.arm()
    assert wait_until(lambda: rig.win.session_count == 1)
    QTest.qWait(1500)
    assert len(rig.files()) == 1 and rig.win.session_count == 1


def test_same_barcode_back_again_becomes_attempt_2(rig):
    rig.win._apply_scan_area(ITEM2_BOX)
    rig.arm()
    assert wait_until(lambda: rig.win.session_count == 1)

    rig.show(None)                       # item leaves
    QTest.qWait(1200)
    rig.show("SN-DEMO-0002")             # and comes back
    assert wait_until(lambda: rig.win.session_count == 2)

    names = [p.name for p in rig.files()]
    assert names == ["SN-DEMO-0002_attempt_1.jpg", "SN-DEMO-0002_attempt_2.jpg"]


def test_barcode_outside_the_scan_area_is_ignored(rig):
    rig.win._apply_scan_area(EMPTY_CORNER)
    rig.arm()
    QTest.qWait(2500)
    assert rig.win.session_count == 0 and rig.files() == []
    assert rig.win.engine.tick(time.monotonic()).status.kind == "watching"


def test_stop_prevents_capture_and_start_resumes(rig):
    rig.win._apply_scan_area(ITEM2_BOX)
    assert not rig.win.engine.running and "START" in rig.win.start_btn.text()
    QTest.qWait(2000)
    assert rig.win.session_count == 0 and rig.files() == []
    assert not rig.win.capture_btn.isEnabled()

    rig.win.start_btn.click()
    assert "STOP" in rig.win.start_btn.text()
    assert wait_until(lambda: rig.win.session_count == 1)


def test_edit_draws_and_saves_the_scan_area(rig):
    win = rig.win
    win.edit_btn.click()
    assert win._editing and win.engine.paused and not win.start_btn.isEnabled()
    assert "Done" in win.edit_btn.text() and not win.cancel_btn.isHidden()

    rig.drag(rig.pos(0.12, 0.45), rig.pos(0.46, 0.80))
    win.edit_btn.click()                 # Done
    rig.arm()

    assert not win._editing and not win.engine.paused and win.start_btn.isEnabled()
    assert win.roi.x == pytest.approx(0.12, abs=0.01) and win.roi.right == pytest.approx(0.46, abs=0.01)
    saved = config.load_settings()["roi"]
    assert saved == pytest.approx(win.roi.to_list())
    assert wait_until(lambda: win.session_count == 1), "the new box covers the barcode, so it is captured"


def test_nothing_is_captured_while_editing(rig):
    rig.arm()
    rig.win.edit_btn.click()
    QTest.qWait(2000)                     # barcode is in view the whole time
    assert rig.win.session_count == 0 and rig.files() == []

    rig.win.cancel_btn.click()            # no scan area -> whole frame; capture resumes
    assert wait_until(lambda: rig.win.session_count == 1)


def test_cancel_and_escape_keep_the_old_scan_area(rig):
    rig.win._apply_scan_area(EMPTY_CORNER)
    rig.win.edit_btn.click()
    rig.drag(rig.pos(0.1, 0.1), rig.pos(0.4, 0.4))
    rig.win.cancel_btn.click()
    assert rig.win.roi == EMPTY_CORNER and config.load_settings()["roi"] == EMPTY_CORNER.to_list()

    rig.win.edit_btn.click()
    rig.drag(rig.pos(0.1, 0.1), rig.pos(0.4, 0.4))
    rig.win._escape()
    assert rig.win.roi == EMPTY_CORNER and not rig.win._editing


def test_clear_box_goes_back_to_scanning_the_whole_frame(rig):
    rig.win._apply_scan_area(EMPTY_CORNER)
    rig.win.edit_btn.click()
    rig.win.clear_btn.click()
    rig.win.edit_btn.click()
    rig.arm()
    assert rig.win.roi is None and config.load_settings()["roi"] is None
    assert wait_until(lambda: rig.win.session_count == 1)


def test_scan_area_is_restored_on_next_launch(rig, qapp):
    rig.win._apply_scan_area(ITEM2_BOX)
    again = MainWindow(DemoCamera(320, 240), config.load_settings(), demo=True)
    try:
        assert again.roi == ITEM2_BOX
    finally:
        again.close()


def test_manual_capture_saves_under_the_typed_barcode(rig):
    rig.show(None)                        # nothing in view
    rig.win._apply_scan_area(EMPTY_CORNER)
    rig.arm()
    rig.win.manual_entry.setText("HAND/TYPED:1")
    rig.win.capture_btn.click()

    assert wait_until(lambda: rig.win.session_count == 1)
    assert [p.name for p in rig.files()] == ["HAND_TYPED_1_attempt_1.jpg"]
    assert "manual entry" in (rig.files()[0].parent / "log.txt").read_text(encoding="utf-8")
    assert rig.win.manual_entry.text() == ""


def test_manual_capture_is_blocked_while_stopped_and_needs_a_barcode(rig):
    rig.show(None)
    rig.win.manual_entry.setText("X1")
    assert not rig.win.capture_btn.isEnabled()
    rig.win._manual_capture()             # Enter in the field must not bypass Stop
    QTest.qWait(500)
    assert rig.files() == []

    rig.arm()
    rig.win.manual_entry.clear()
    rig.win.capture_btn.click()           # empty field
    assert rig.boxes and rig.boxes[-1][0] == "warning" and rig.files() == []


def test_save_failure_is_reported_not_swallowed(rig, tmp_path):
    blocker = tmp_path / "not_a_folder"
    blocker.write_text("a file where the output folder should be")
    rig.win.settings["output_dir"] = str(blocker)
    rig.win._apply_scan_area(ITEM2_BOX)
    rig.arm()

    assert wait_until(lambda: any(kind == "critical" for kind, _ in rig.boxes))
    assert rig.win.session_count == 0
    assert "Save failed" in rig.boxes[-1][1][1]
    assert rig.win.engine.tick(time.monotonic()).status.kind in ("failed", "already", "watching")


def test_saved_barcode_shows_in_the_history_and_counter(rig):
    rig.win._apply_scan_area(ITEM2_BOX)
    rig.arm()
    assert wait_until(lambda: rig.win.session_count == 1)
    assert rig.win.history_box.count() == 1
    assert rig.win.count_label.text() == "1"


def test_settings_changes_reach_the_engine_and_file(rig, monkeypatch):
    win = rig.win
    applied, reconnects = [], []
    monkeypatch.setattr(win.camera, "apply_settings", applied.append)
    monkeypatch.setattr(win, "_reconnect", lambda: reconnects.append(1))

    win._apply_settings({"capture_delay_seconds": 3.0, "barcode_lost_reset_seconds": 4.0,
                         "barcode_stable_reads": 3, "output_dir": "/somewhere/else"})
    assert (win.engine.capture_delay, win.engine.lost_reset) == (3.0, 4.0)
    assert config.load_settings()["output_dir"] == "/somewhere/else"
    assert not applied and not reconnects

    win._apply_settings({"exposure_auto": False, "exposure_us": 4000.0})
    assert len(applied) == 1 and applied[0].exposure_us == 4000.0 and not reconnects

    win._apply_settings({"camera_serial": "DA999"})
    assert reconnects == [1]


def test_window_closes_cleanly_with_a_save_in_flight(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.json")
    settings = config.load_settings()
    settings.update(output_dir=str(tmp_path / "captures"), capture_delay_seconds=0.2)
    camera = DemoCamera(1368, 912)
    camera._current_serial = lambda: "SN-DEMO-0002"
    win = MainWindow(camera, settings, demo=True)
    win.show()
    assert wait_until(lambda: win.engine._saving or win.session_count > 0, timeout=10)
    win.close()
    assert not win.worker.isRunning()
    assert list((tmp_path / "captures").rglob("*.jpg")), "the image being written was finished before exit"
