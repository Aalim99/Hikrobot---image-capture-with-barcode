"""The operator window: live view, scan-area editing, Start/Stop, history."""
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFontMetrics, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import config as config_module
import roi as roi_mod
from barcode_scanner import BACKEND_NAME
from capture_engine import CaptureEngine
from hik_camera import CameraSettings
from ui.settings_dialog import SettingsDialog
from ui.theme import C, mono, pill_style, sans
from ui.video_view import VideoView
from ui.workers import SaveSignals, SaveTask, VisionWorker

TICK_MS = 30
HISTORY_LIMIT = 10
STALE_FRAME_SECONDS = 3.0

# engine status kind -> (pill text, pill colour, barcode-readout colour)
PILLS = {
    "stopped": ("STOPPED", "muted", "dim"),
    "paused": ("PAUSED", "muted", "dim"),
    "countdown": ("BARCODE FOUND", "warn", "text"),
    "saving": ("SAVING…", "accent", "text"),
    "saved": ("SAVED", "ok", "ok"),
    "failed": ("SAVE FAILED", "err", "dim"),
    "blocked": ("CAMERA NOT READY", "err", "dim"),
    "already": ("ALREADY CAPTURED", "accent", "muted"),
    "unreadable": ("BARCODE UNREADABLE", "warn", "dim"),
    "watching": ("WATCHING", "accent", "dim"),
}


def _card():
    frame = QFrame()
    frame.setObjectName("card")
    return frame


def _caption(text):
    label = QLabel(text)
    label.setObjectName("caption")
    return label


def _set_variant(widget, variant):
    widget.setProperty("variant", variant)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def _button(text, variant=None, callback=None):
    button = QPushButton(text)
    button.setFocusPolicy(Qt.NoFocus)   # keep keyboard focus on the view / entry field
    button.setCursor(Qt.PointingHandCursor)
    if variant:
        button.setProperty("variant", variant)
    if callback:
        button.clicked.connect(callback)
    return button


class MainWindow(QMainWindow):
    def __init__(self, camera, settings, demo=False):
        super().__init__()
        self.setWindowTitle("Barcode Image Capture")
        self.resize(1360, 880)
        self.setMinimumSize(1100, 720)

        self.camera = camera
        self.settings = settings
        self.demo = demo

        self.engine = CaptureEngine(settings["capture_delay_seconds"],
                                    settings["barcode_lost_reset_seconds"])
        self.roi = roi_mod.Roi.from_list(settings.get("roi"))
        self.session_count = 0
        self.history = []

        self._editing = False
        self._dialog_open = False
        self._have_frame = False
        self._result_times = deque(maxlen=12)
        self._shown_status = None

        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)        # saves run one at a time, in order
        self.save_signals = SaveSignals()
        self.save_signals.saved.connect(self._on_saved)
        self.save_signals.failed.connect(self._on_save_failed)

        self._build_ui()
        self._refresh_buttons()
        self.view.set_roi(self.roi)

        self.worker = VisionWorker(camera, settings["barcode_stable_reads"], self.roi, self)
        self.worker.resultReady.connect(self._on_result)
        self.worker.start()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(TICK_MS)

        QShortcut(QKeySequence("F2"), self, activated=self._toggle_running)
        QShortcut(QKeySequence("F3"), self, activated=self._toggle_edit)
        QShortcut(QKeySequence("F5"), self, activated=self._reconnect)
        QShortcut(QKeySequence("Escape"), self, activated=self._escape)

    # ---------- layout ----------

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)

        layout.addLayout(self._build_header())

        body = QHBoxLayout()
        body.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(14)
        left.addWidget(self._build_video_card(), 1)
        left.addWidget(self._build_barcode_card())
        body.addLayout(left, 1)
        body.addWidget(self._build_sidebar())
        layout.addLayout(body, 1)

        layout.addLayout(self._build_footer())

    def _build_header(self):
        header = QHBoxLayout()
        header.setSpacing(10)

        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("BARCODE CAPTURE")
        title.setObjectName("title")
        subtitle = QLabel("DEMO MODE · synthetic camera" if self.demo
                          else "one image per barcode, saved in the barcode's own folder")
        subtitle.setObjectName("subtitle")
        if self.demo:
            subtitle.setStyleSheet(f"color: {C['warn']};")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch()

        self.pill = QLabel()
        self.pill.setMinimumWidth(230)
        self.pill.setAlignment(Qt.AlignCenter)
        header.addWidget(self.pill)
        header.addSpacing(8)

        self.clear_btn = _button("Clear box", "ghost", self._clear_box)
        self.cancel_btn = _button("Cancel", "ghost", lambda: self._finish_edit(False))
        self.edit_btn = _button("", None, self._toggle_edit)
        self.start_btn = _button("", None, self._toggle_running)
        for button in (self.clear_btn, self.cancel_btn, self.edit_btn, self.start_btn):
            button.setMinimumHeight(46)
            header.addWidget(button)
        return header

    def _build_video_card(self):
        card = _card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)

        bar = QHBoxLayout()
        bar.addWidget(_caption("LIVE VIEW"))
        self.camera_title = QLabel(self.camera.title)
        self.camera_title.setObjectName("hint")
        bar.addWidget(self.camera_title)
        bar.addStretch()
        decoder = QLabel(f"barcode decoder: {BACKEND_NAME}")
        decoder.setObjectName("hint")
        bar.addWidget(decoder)
        bar.addSpacing(12)
        self.live_chip = QLabel("STARTING")
        bar.addWidget(self.live_chip)
        layout.addLayout(bar)

        self.view = VideoView()
        self.view.editChanged.connect(self._refresh_buttons)
        self.view.editFinished.connect(self._finish_edit)
        layout.addWidget(self.view, 1)
        return card

    def _build_barcode_card(self):
        card = _card()
        layout = QHBoxLayout(card)
        layout.setContentsMargins(22, 14, 22, 16)

        left = QVBoxLayout()
        left.setSpacing(0)
        left.addWidget(_caption("BARCODE"))
        self.sn_label = QLabel("—")
        self.sn_label.setFont(mono(30, 700))
        left.addWidget(self.sn_label)
        layout.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(10)
        self.detail_label = QLabel("")
        self.detail_label.setFont(sans(11, 700))
        self.detail_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(360)
        right.addWidget(self.detail_label)
        right.addWidget(self.progress)
        layout.addLayout(right)
        return card

    def _build_sidebar(self):
        side = QWidget()
        side.setFixedWidth(310)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        counter = _card()
        box = QVBoxLayout(counter)
        box.setContentsMargins(18, 14, 18, 14)
        box.setSpacing(0)
        box.addWidget(_caption("CAPTURED THIS SESSION"))
        self.count_label = QLabel("0")
        self.count_label.setObjectName("bigcount")
        box.addWidget(self.count_label)
        layout.addWidget(counter)

        recent = _card()
        box = QVBoxLayout(recent)
        box.setContentsMargins(14, 14, 14, 14)
        box.setSpacing(8)
        title = _caption("RECENT CAPTURES")
        title.setContentsMargins(4, 0, 0, 0)
        box.addWidget(title)
        self.history_box = QVBoxLayout()
        self.history_box.setSpacing(6)
        box.addLayout(self.history_box)
        box.addStretch()
        box.addWidget(_button("Open captures folder", "ghost", self._open_folder))
        layout.addWidget(recent, 1)
        self._render_history()
        return side

    def _build_footer(self):
        footer = QHBoxLayout()
        footer.setSpacing(10)

        label = QLabel("Manual barcode")
        label.setStyleSheet(f"color: {C['muted']};")
        footer.addWidget(label)
        self.manual_entry = QLineEdit()
        self.manual_entry.setPlaceholderText("type it if the label is damaged")
        self.manual_entry.setFont(mono(11))
        self.manual_entry.setFixedWidth(300)
        self.manual_entry.returnPressed.connect(self._manual_capture)
        footer.addWidget(self.manual_entry)
        self.capture_btn = _button("Capture now", "primary", self._manual_capture)
        footer.addWidget(self.capture_btn)

        footer.addStretch()
        self.output_label = QLabel()
        self.output_label.setObjectName("hint")
        footer.addWidget(self.output_label)
        footer.addSpacing(8)
        footer.addWidget(_button("Reconnect  (F5)", None, self._reconnect))
        footer.addWidget(_button("Settings", None, self._open_settings))
        self._refresh_output_label()
        return footer

    # ---------- small renderers ----------

    def _refresh_output_label(self):
        path = self.settings["output_dir"]
        shown = QFontMetrics(self.output_label.font()).elidedText(f"saving to  {path}", Qt.ElideMiddle, 380)
        self.output_label.setText(shown)
        self.output_label.setToolTip(path)

    def _render_history(self):
        while self.history_box.count():
            item = self.history_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self.history:
            empty = QLabel("nothing captured yet")
            empty.setObjectName("hint")
            empty.setContentsMargins(4, 4, 0, 0)
            self.history_box.addWidget(empty)
            return

        metrics = QFontMetrics(mono(10, 700))
        for stamp, sn, attempt in self.history[:HISTORY_LIMIT]:
            row = QFrame()
            row.setStyleSheet(f"QFrame {{ background: {C['panel_alt']}; border-radius: 8px; }}")
            line = QHBoxLayout(row)
            line.setContentsMargins(10, 7, 10, 7)
            when = QLabel(stamp)
            when.setFont(mono(9))
            when.setStyleSheet(f"color: {C['dim']};")
            name = QLabel(metrics.elidedText(sn, Qt.ElideMiddle, 130))
            name.setFont(mono(10, 700))
            name.setToolTip(sn)
            count = QLabel(f"#{attempt}")
            count.setFont(sans(9, 700))
            count.setStyleSheet(f"color: {C['ok']};")
            line.addWidget(when)
            line.addWidget(name, 1)
            line.addWidget(count)
            self.history_box.addWidget(row)

    def _refresh_buttons(self):
        running, editing = self.engine.running, self._editing
        if running:
            self.start_btn.setText("■  STOP   (F2)")
            _set_variant(self.start_btn, "stop")
        else:
            self.start_btn.setText("▶  START   (F2)")
            _set_variant(self.start_btn, "start")
        self.start_btn.setEnabled(not editing)

        if editing:
            self.edit_btn.setText("✓  Done")
            _set_variant(self.edit_btn, "editing")
        else:
            self.edit_btn.setText("✎  Edit area   (F3)")
            _set_variant(self.edit_btn, None)
        self.clear_btn.setVisible(editing)
        self.cancel_btn.setVisible(editing)
        self.clear_btn.setEnabled(self.view.working_roi is not None)

        self.capture_btn.setEnabled(running and not editing)
        self.manual_entry.setEnabled(not editing)

    def _set_pill(self, text, colour_key):
        key = (text, colour_key)
        if key != self._shown_status:
            self._shown_status = key
            self.pill.setText(f"●  {text}")
            self.pill.setStyleSheet(pill_style(colour_key))

    def _render_status(self, status):
        text, pill_colour, sn_colour = PILLS[status.kind]
        if status.kind == "paused" and self._editing:
            text, pill_colour = "EDITING SCAN AREA", "warn"
        self._set_pill(text, pill_colour)

        self.sn_label.setText(status.sn or "—")
        self.sn_label.setStyleSheet(f"color: {C[sn_colour]};")

        detail, detail_colour, fraction = "", "muted", 0.0
        if status.kind == "countdown":
            detail, detail_colour, fraction = f"capturing in {status.remaining:.1f} s", "warn", status.fraction
        elif status.kind == "stopped":
            detail = "Press START (F2) to begin capturing"
        elif status.kind == "paused":
            detail = "Editing the scan area…" if self._editing else "Settings are open"
        elif status.kind == "saving":
            detail = "Writing the image to disk…"
        elif status.kind == "saved":
            detail, detail_colour = status.detail, "ok"
        elif status.kind == "failed":
            detail, detail_colour = status.detail, "err"
        elif status.kind == "blocked":
            detail, detail_colour = status.detail, "err"
        elif status.kind == "already":
            detail = "Remove the item to capture again"
        elif status.kind == "unreadable":
            detail, detail_colour = "Too small or blurred - move closer or zoom in", "warn"
        elif status.kind == "watching":
            detail = ("Present an item with a barcode inside the scan area" if self.roi
                      else "Draw a scan area with  ✎ Edit area  for faster, more reliable reading")
        self.detail_label.setText(detail)
        self.detail_label.setStyleSheet(f"color: {C[detail_colour]};")
        self.progress.setValue(int(min(max(fraction, 0.0), 1.0) * 1000))

    def _update_live_chip(self, now):
        if not self.camera.connected:
            text, colour = "OFFLINE", "err"
        elif not self._have_frame:
            text, colour = "STARTING", "warn"
        else:
            fps = ""
            if len(self._result_times) >= 3:
                span = self._result_times[-1] - self._result_times[0]
                if span > 0:
                    fps = f"  ·  {(len(self._result_times) - 1) / span:.1f} fps"
            text, colour = f"LIVE{fps}", "ok"
        self.live_chip.setText(text)
        self.live_chip.setStyleSheet(f"color: {C[colour]}; font-weight: 700; font-size: 9pt;")
        title = self.camera.title
        if self.camera_title.text() != title:
            self.camera_title.setText(title)

    # ---------- the loop ----------

    def _on_result(self, result):
        now = time.monotonic()
        self._have_frame = True
        self._result_times.append(now)
        self.view.show_frame(result.preview, *result.frame_size)
        if result.generation != self.worker.generation:
            return   # scanned under settings that have since changed (old scan area...)
        self.view.set_overlays(result.detections, result.unreadable_rect)
        if result.scanned:
            self.engine.observe(now, result.stable_sn, result.raw_detected,
                                unreadable=result.unreadable_rect is not None and not result.detections)

    def _tick(self):
        now = time.monotonic()
        if not self.camera.connected:
            self._have_frame = False
            self.view.show_offline("NO SIGNAL", self.camera.error or "camera not connected")
        elif not self._have_frame:
            self.view.show_offline("STARTING…", "waiting for the first frame")
        elif self._result_times and now - self._result_times[-1] > STALE_FRAME_SECONDS:
            self._have_frame = False

        tick = self.engine.tick(now, self._blocked_reason())
        self._render_status(tick.status)
        self._update_live_chip(now)
        if tick.capture_sn:
            self._request_capture(tick.capture_sn, "automatic (barcode read in the scan area)")

    def _blocked_reason(self):
        if not self.camera.connected:
            return "camera offline"
        if self.camera.latest() is None:
            return "waiting for the first frame"
        return None

    # ---------- capturing ----------

    def _scan_area_text(self):
        if self.roi is None:
            return "whole frame"
        return f"x {self.roi.x:.1%}, y {self.roi.y:.1%}, w {self.roi.w:.1%}, h {self.roi.h:.1%} of the frame"

    def _request_capture(self, sn, trigger):
        now = time.monotonic()
        frame = self.camera.latest()
        if frame is None:
            self.engine.report_failed("no frame available from the camera", now)
            return
        details = {
            **self.camera.details(),
            "Trigger": trigger,
            "Scan area": self._scan_area_text(),
            "Capture delay": f"{self.settings['capture_delay_seconds']:.1f} s",
        }
        self.pool.start(SaveTask(self.save_signals, self.settings["output_dir"], sn, frame,
                                 details, self.settings["jpeg_quality"]))

    def _on_saved(self, saved, sn):
        self.session_count += 1
        self.count_label.setText(str(self.session_count))
        self.history.insert(0, (datetime.now().strftime("%H:%M:%S"), sn, saved.attempt))
        del self.history[HISTORY_LIMIT:]
        self._render_history()
        self.engine.report_saved(sn, saved.attempt, time.monotonic())

    def _on_save_failed(self, message, sn):
        self.engine.report_failed(message, time.monotonic())
        self._modal(lambda: QMessageBox.critical(
            self, "Save failed", f"Could not save the image for {sn}:\n\n{message}\n\n"
            f"Output folder: {self.settings['output_dir']}"))

    def _manual_capture(self):
        if not self.engine.running or self._editing:
            return  # Enter in the field must not bypass Stop
        sn = self.manual_entry.text().strip()
        if not sn:
            self._modal(lambda: QMessageBox.warning(self, "Manual capture", "Enter a barcode first."))
            return
        blocked = self._blocked_reason()
        if blocked:
            self._modal(lambda: QMessageBox.critical(self, "Capture failed", f"Cannot capture: {blocked}."))
            return
        self.engine.begin_capture(sn, time.monotonic())
        self._request_capture(sn, "manual entry")
        self.manual_entry.clear()

    def _modal(self, show):
        """Run a blocking message box with auto-capture held off."""
        self._dialog_open = True
        self._update_pause()
        try:
            show()
        finally:
            self._dialog_open = False
            self._update_pause()

    # ---------- start / stop ----------

    def _toggle_running(self):
        if self._editing:
            return
        if self.engine.running:
            self.engine.stop()
        else:
            self.engine.start()
        self._refresh_buttons()

    def _update_pause(self):
        self.engine.set_paused(self._editing or self._dialog_open)

    # ---------- scan-area editing ----------

    def _toggle_edit(self):
        if self._editing:
            self._finish_edit(True)
        elif not self._dialog_open:
            self._begin_edit()

    def _begin_edit(self):
        self._editing = True
        self._update_pause()
        self.worker.set_scanning(False)
        self.engine.reset_observation()
        self.view.set_overlays([], None)
        self.view.begin_edit()
        self._refresh_buttons()

    def _finish_edit(self, commit):
        if not self._editing:
            return
        box = self.view.end_edit(commit)
        self._editing = False
        if commit:
            self._apply_scan_area(box)
        self.worker.set_scanning(True)
        self.engine.reset_observation()
        self._update_pause()
        self._refresh_buttons()

    def _clear_box(self):
        self.view.clear_working_roi()
        self._refresh_buttons()

    def _escape(self):
        if self._editing:
            self._finish_edit(False)

    def _apply_scan_area(self, box):
        self.roi = box
        self.settings["roi"] = box.to_list() if box else None
        config_module.save_settings(self.settings)
        self.worker.set_scan_area(box)
        self.engine.reset_observation()
        self.view.set_roi(box)

    # ---------- settings / camera ----------

    def _open_settings(self):
        if self._editing or self._dialog_open:
            return
        self._dialog_open = True
        self._update_pause()
        try:
            dialog = SettingsDialog(self, self.settings, demo=self.demo)
            if dialog.exec():
                self._apply_settings(dialog.values())
        finally:
            self._dialog_open = False
            self._update_pause()

    def _apply_settings(self, new):
        old = self.settings
        device_changed = new.get("camera_serial", old["camera_serial"]) != old["camera_serial"]
        tuning_changed = any(new.get(key, old[key]) != old[key] for key in
                             ("exposure_auto", "exposure_us", "gain_auto", "gain_db", "acquisition_fps"))
        old.update(new)
        config_module.save_settings(old)

        self.engine.capture_delay = old["capture_delay_seconds"]
        self.engine.lost_reset = old["barcode_lost_reset_seconds"]
        self.worker.set_stable_reads(old["barcode_stable_reads"])
        self._refresh_output_label()

        if device_changed:
            self._reconnect()
        elif tuning_changed:
            self.camera.apply_settings(CameraSettings.from_dict(old))

    def _reconnect(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self._have_frame = False
            self.camera.reconfigure(CameraSettings.from_dict(self.settings))
        finally:
            QApplication.restoreOverrideCursor()

    def _open_folder(self):
        folder = Path(self.settings["output_dir"])
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # ---------- shutdown ----------

    def closeEvent(self, event):
        self.timer.stop()
        self.worker.requestInterruption()
        self.worker.wait(3000)
        self.pool.waitForDone(15000)   # let any image still being written finish
        self.camera.close()
        super().closeEvent(event)
