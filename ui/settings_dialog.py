"""Settings dialog. Returns the edited values; the main window applies them."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from shiboken6 import isValid

import hik_camera


def _section(title):
    card = QFrame()
    card.setObjectName("card")
    outer = QVBoxLayout(card)
    outer.setContentsMargins(18, 14, 18, 16)
    outer.setSpacing(10)
    caption = QLabel(title.upper())
    caption.setObjectName("caption")
    outer.addWidget(caption)
    form = QFormLayout()
    form.setHorizontalSpacing(16)
    form.setVerticalSpacing(10)
    outer.addLayout(form)
    return card, form, outer


def _note(text=""):
    label = QLabel(text)
    label.setObjectName("hint")
    label.setWordWrap(True)
    return label


def _row(*widgets):
    row = QHBoxLayout()
    row.setSpacing(12)
    for widget in widgets:
        row.addWidget(widget)
    return row


def _spin(low, high, value, decimals=None, step=None, suffix=""):
    box = QDoubleSpinBox() if decimals is not None else QSpinBox()
    if decimals is not None:
        box.setDecimals(decimals)
    box.setRange(low, high)
    if step:
        box.setSingleStep(step)
    box.setValue(value)
    box.setSuffix(suffix)
    box.setFixedWidth(150)
    return box


class SettingsDialog(QDialog):
    def __init__(self, parent, settings: dict, demo: bool = False, on_balance_once=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(1000)
        self._settings = settings
        self._demo = demo
        self._on_balance_once = on_balance_once   # callable -> bool, measures white balance now

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        # ---- saving ----
        saving, form, saving_box = _section("Saving")
        self.output_dir = QLineEdit(settings["output_dir"])
        browse = QPushButton("Browse…")
        browse.setProperty("variant", "ghost")
        browse.clicked.connect(self._browse)
        row = _row(self.output_dir, browse)
        row.setStretch(0, 1)
        form.addRow("Output folder", row)
        self.image_format = QComboBox()
        self.image_format.addItem("JPEG", "jpg")
        self.image_format.addItem("PNG (lossless)", "png")
        self.image_format.setCurrentIndex(max(self.image_format.findData(settings.get("image_format", "jpg")), 0))
        form.addRow("Image format", self.image_format)
        self.jpeg_quality = _spin(1, 100, int(settings["jpeg_quality"]))
        form.addRow("JPEG quality", self.jpeg_quality)
        saving_box.addWidget(_note("PNG keeps every pixel exactly, but the files are several times larger "
                                   "than JPEG and a 20 MP picture can take a few seconds to write."))
        self.image_format.currentIndexChanged.connect(self._format_changed)
        self._format_changed()

        # ---- camera ----
        camera, form, camera_box = _section("Camera")
        self.camera_combo = QComboBox()
        refresh = QPushButton("Refresh")
        refresh.setProperty("variant", "ghost")
        refresh.clicked.connect(self._refresh_cameras)
        row = _row(self.camera_combo, refresh)
        row.setStretch(0, 1)
        form.addRow("Device", row)

        self.exposure_auto = QCheckBox("Auto")
        self.exposure_us = _spin(20, 800000, float(settings["exposure_us"]), decimals=0, step=500, suffix=" µs")
        row = _row(self.exposure_auto, self.exposure_us)
        row.addStretch()
        form.addRow("Exposure", row)

        self.gain_auto = QCheckBox("Auto")
        self.gain_db = _spin(0, 48, float(settings["gain_db"]), decimals=1, step=0.5, suffix=" dB")
        row = _row(self.gain_auto, self.gain_db)
        row.addStretch()
        form.addRow("Gain", row)

        self.wb_combo = QComboBox()
        self.wb_combo.addItem("Camera default (don't change)", "camera")
        self.wb_combo.addItem("Auto", "auto")
        self.wb_combo.addItem("Off", "off")
        self.wb_combo.setCurrentIndex(max(self.wb_combo.findData(settings.get("white_balance", "camera")), 0))
        self.balance_btn = QPushButton("Balance now")
        self.balance_btn.setProperty("variant", "ghost")
        self.balance_btn.setToolTip("Point the camera at a white or grey sheet, then click.")
        self.balance_btn.clicked.connect(self._balance_now)
        row = _row(self.wb_combo, self.balance_btn)
        row.setStretch(0, 1)
        form.addRow("White balance", row)

        self.gamma_override = QCheckBox("Set")
        self.gamma = _spin(0.1, 4.0, float(settings.get("gamma", 1.0)), decimals=2, step=0.1)
        self.gamma_override.setChecked(bool(settings.get("gamma_override", False)))
        self.gamma.setEnabled(self.gamma_override.isChecked())
        self.gamma_override.toggled.connect(self.gamma.setEnabled)
        row = _row(self.gamma_override, self.gamma)
        row.addStretch()
        form.addRow("Gamma", row)

        self.fps = _spin(1, 60, float(settings["acquisition_fps"]), decimals=1, step=1, suffix=" fps")
        form.addRow("Frame rate cap", self.fps)
        camera_box.addWidget(_note("A full 20 MP frame is ~20 MB, so ~10 fps is plenty for reading "
                                   "barcodes and keeps the USB 3.0 link comfortable."))
        camera_box.addWidget(_note("'Camera default' puts back whatever the camera had when it connected. "
                                   "Settings a camera does not support are skipped; a warning then appears "
                                   "next to the camera name."))
        self.balance_note = _note()
        self.balance_note.hide()
        camera_box.addWidget(self.balance_note)
        self.camera_note = _note()
        self.camera_note.hide()
        camera_box.addWidget(self.camera_note)

        self.exposure_auto.setChecked(bool(settings["exposure_auto"]))
        self.gain_auto.setChecked(bool(settings["gain_auto"]))
        self.exposure_auto.toggled.connect(lambda on: self.exposure_us.setEnabled(not on))
        self.gain_auto.toggled.connect(lambda on: self.gain_db.setEnabled(not on))
        self.exposure_us.setEnabled(not self.exposure_auto.isChecked())
        self.gain_db.setEnabled(not self.gain_auto.isChecked())

        # ---- auto capture ----
        auto, form, _ = _section("Auto-capture")
        self.delay = _spin(0, 60, float(settings["capture_delay_seconds"]), decimals=1, step=0.5, suffix=" s")
        form.addRow("Delay after barcode is read", self.delay)
        self.lost_reset = _spin(0, 60, float(settings["barcode_lost_reset_seconds"]), decimals=1, step=0.5, suffix=" s")
        form.addRow("Barcode gone before re-capture", self.lost_reset)
        self.stable_reads = _spin(1, 10, int(settings["barcode_stable_reads"]))
        form.addRow("Identical reads required", self.stable_reads)

        # two columns: Saving + Auto-capture on the left, Camera on the right
        columns = QHBoxLayout()
        columns.setSpacing(14)
        left, right = QVBoxLayout(), QVBoxLayout()
        left.setSpacing(14)
        left.addWidget(saving)
        left.addWidget(auto)
        left.addStretch()
        right.addWidget(camera)
        right.addStretch()
        columns.addLayout(left, 1)
        columns.addLayout(right, 1)
        root.addLayout(columns)

        # ---- buttons ----
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setProperty("variant", "primary")
        save.setDefault(True)
        save.clicked.connect(self._accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root.addLayout(buttons)

        if demo:
            for widget in (self.camera_combo, refresh, self.exposure_auto, self.exposure_us,
                           self.gain_auto, self.gain_db, self.fps, self.wb_combo, self.balance_btn,
                           self.gamma_override, self.gamma):
                widget.setEnabled(False)
            self.camera_note.setText("Demo mode: the camera is simulated, so camera settings are off.")
            self.camera_note.show()
            self.camera_combo.addItem("Demo camera (synthetic)", "")
        else:
            self._fill_cameras()
        if self._on_balance_once is None:
            self.balance_btn.setEnabled(False)

    def _format_changed(self):
        self.jpeg_quality.setEnabled(self.image_format.currentData() == "jpg")

    def _balance_now(self):
        ok = bool(self._on_balance_once and self._on_balance_once())
        self.balance_note.setText(
            "White balance measured from what the camera sees now." if ok
            else "The camera did not accept it (not connected, or not supported by this camera).")
        self.balance_note.show()
        QTimer.singleShot(0, self._fit_height)

    def _fill_cameras(self):
        current = self._settings["camera_serial"]
        self.camera_combo.clear()
        self.camera_combo.addItem("Automatic - first camera found", "")
        try:
            devices = hik_camera.list_devices()
            note = "" if devices else "No camera found - check the USB 3.0 cable."
        except hik_camera.SdkError as exc:
            devices, note = [], str(exc)
        self.camera_note.setText(note)
        self.camera_note.setVisible(bool(note))
        for device in devices:
            self.camera_combo.addItem(device.label, device.serial)
        index = self.camera_combo.findData(current)
        if index < 0 and current:
            self.camera_combo.addItem(f"{current}  (not connected)", current)
            index = self.camera_combo.count() - 1
        self.camera_combo.setCurrentIndex(max(index, 0))

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_height)

    def _fit_height(self):
        # Word-wrapped notes need more height than the dialog's first size
        # estimate gives them, which squashes the controls above them.
        if not isValid(self):   # the deferred call can outlive the dialog
            return
        needed = self.layout().totalHeightForWidth(self.width())
        if needed > self.height():
            self.resize(self.width(), needed)

    def _refresh_cameras(self):
        self._fill_cameras()
        QTimer.singleShot(0, self._fit_height)   # a note may have appeared

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "Choose output folder", self.output_dir.text())
        if path:
            self.output_dir.setText(path)

    def _accept(self):
        if not self.output_dir.text().strip():
            self.output_dir.setFocus()
            self.output_dir.setPlaceholderText("Choose where images are saved")
            return
        self.accept()

    def values(self) -> dict:
        """The settings as edited (merge into the existing dict)."""
        values = {
            "output_dir": self.output_dir.text().strip(),
            "image_format": self.image_format.currentData(),
            "jpeg_quality": int(self.jpeg_quality.value()),
            "capture_delay_seconds": float(self.delay.value()),
            "barcode_lost_reset_seconds": float(self.lost_reset.value()),
            "barcode_stable_reads": int(self.stable_reads.value()),
        }
        if not self._demo:
            values.update({
                "camera_serial": self.camera_combo.currentData() or "",
                "exposure_auto": self.exposure_auto.isChecked(),
                "exposure_us": float(self.exposure_us.value()),
                "gain_auto": self.gain_auto.isChecked(),
                "gain_db": float(self.gain_db.value()),
                "white_balance": self.wb_combo.currentData(),
                "gamma_override": self.gamma_override.isChecked(),
                "gamma": float(self.gamma.value()),
                "acquisition_fps": float(self.fps.value()),
            })
        return values
