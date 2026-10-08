"""Background work, so a 20 MP frame never stalls the window.

VisionWorker  newest frame -> downscaled preview + barcode scan of the scan
              area only. Skips frames when it cannot keep up (always works on
              the latest one, never a backlog).
SaveTask      converts the full frame, JPEG-encodes it and writes it to disk.
"""
import sys
import time
import traceback
from dataclasses import dataclass

import cv2
import numpy as np
from PySide6.QtCore import QObject, QRunnable, QThread, Signal

import roi as roi_mod
from barcode_scanner import StableBarcodeDetector, scan_frame
from capture_session import save_capture

PREVIEW_MAX_DIM = 1600
UNREADABLE_PROBE_INTERVAL = 0.4   # the "barcode present but unreadable?" probe is slower
MIN_CYCLE = 0.04                  # never spin faster than ~25 scans/s


@dataclass
class VisionResult:
    frame_id: int
    preview: np.ndarray            # (h, w, 3) RGB
    frame_size: tuple              # full-resolution (width, height)
    detections: list               # in full-resolution frame pixels
    unreadable_rect: tuple | None
    stable_sn: str | None          # barcode confirmed over enough consecutive scans
    raw_detected: bool             # any barcode seen at all this scan
    scanned: bool                  # False while scanning is switched off
    generation: int = 0            # which scan settings produced this (see VisionWorker)


class VisionWorker(QThread):
    resultReady = Signal(object)

    def __init__(self, camera, stable_reads=2, scan_area=None, parent=None):
        super().__init__(parent)
        self._camera = camera
        self._stable_reads = stable_reads
        self._scan_area = scan_area
        self._scanning = True
        self._reset_detector = True
        self.generation = 0   # bumped whenever the scan settings change

    # Called from the UI thread; plain attribute writes are atomic enough here.
    # Results already queued for the UI were made with the old settings, so
    # each result carries the generation it was produced under and the UI
    # drops stale ones.
    def set_scan_area(self, box):
        self._scan_area = box
        self._changed()

    def set_stable_reads(self, count):
        self._stable_reads = count
        self._changed()

    def set_scanning(self, scanning: bool):
        self._scanning = scanning
        self._changed()

    def _changed(self):
        self._reset_detector = True
        self.generation += 1

    def run(self):
        last_id = None
        last_probe = 0.0
        detector = StableBarcodeDetector(self._stable_reads)
        while not self.isInterruptionRequested():
            frame = self._camera.latest()
            if frame is None or frame.frame_id == last_id:
                self.msleep(15)
                continue
            last_id = frame.frame_id
            started = time.monotonic()
            generation = self.generation

            if self._reset_detector:
                self._reset_detector = False
                detector = StableBarcodeDetector(self._stable_reads)
            probe = started - last_probe >= UNREADABLE_PROBE_INTERVAL
            if probe:
                last_probe = started

            try:
                result = self._process(frame, detector, probe)
                result.generation = generation
            except Exception:  # a decode/convert hiccup must not kill the worker
                traceback.print_exc(file=sys.stderr)
                self.msleep(200)
                continue
            self.resultReady.emit(result)

            spare = MIN_CYCLE - (time.monotonic() - started)
            if spare > 0:
                self.msleep(int(spare * 1000))

    def _process(self, frame, detector, probe):
        bgr, _scale = frame.preview(PREVIEW_MAX_DIM)
        preview = np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))

        scanned, area = self._scanning, self._scan_area
        detections, unreadable, stable, raw = [], None, None, False
        if scanned:
            pixels = roi_mod.to_pixels(area, frame.width, frame.height) if area else None
            scan = scan_frame(frame, pixels, check_unreadable=probe)
            detections, unreadable = scan.detections, scan.unreadable_rect
            stable = detector.update_from_texts([d.text for d in detections])
            raw = detector.raw_detected
        return VisionResult(frame.frame_id, preview, (frame.width, frame.height),
                            detections, unreadable, stable, raw, scanned)


class SaveSignals(QObject):
    saved = Signal(object, str)    # SavedCapture, barcode
    failed = Signal(str, str)      # message, barcode


class SaveTask(QRunnable):
    def __init__(self, signals, output_dir, sn, frame, details, jpeg_quality):
        super().__init__()
        self._signals = signals
        self._args = (output_dir, sn, frame, details, jpeg_quality)

    def run(self):
        output_dir, sn, frame, details, quality = self._args
        try:
            saved = save_capture(output_dir, sn, frame.bgr(), details=details, jpeg_quality=quality)
        except Exception as exc:  # OSError (disk/permissions), cv2.error, MemoryError...
            self._signals.failed.emit(f"{type(exc).__name__}: {exc}", sn)
        else:
            self._signals.saved.emit(saved, sn)
