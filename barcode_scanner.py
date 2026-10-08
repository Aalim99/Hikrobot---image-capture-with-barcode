"""Barcode/QR decoding from a camera frame, with debounce.

The decoder backend is picked at import time, in preference order:

  zxing-cpp  broadest coverage (QR, DataMatrix, Code128, Code39, Aztec,
             PDF417, EAN/UPC...). Ships self-contained wheels, so it needs
             no extra system libraries - this is the one to have.
  pyzbar     ZBar. Good coverage but no DataMatrix, and on Windows its DLL
             needs the VC++ 2013 runtime installed, which often isn't.
  opencv     always present, but QR codes only.

Nothing here raises if a backend is missing: the app falls back and reports
which decoder is live, rather than refusing to start.

The camera frame is ~20 MP, so the app decodes only the scan area the
operator drew (scan_frame) instead of the whole picture.
"""
from dataclasses import dataclass

import cv2

KNOWN_BACKENDS = ("zxing-cpp", "pyzbar", "opencv")


@dataclass(frozen=True)
class Detection:
    text: str
    rect: tuple  # (left, top, width, height) in frame pixels
    symbology: str


@dataclass(frozen=True)
class ScanResult:
    detections: list
    unreadable_rect: tuple | None  # a barcode is visible but cannot be decoded


def _new_linear_detector():
    """OpenCV's 1D barcode detector, if this build has it (4.8+)."""
    try:
        return cv2.barcode.BarcodeDetector()
    except (AttributeError, cv2.error):
        return None


_presence_detector = _new_linear_detector()


def locate_unreadable_barcode(frame_bgr):
    """Find a 1D barcode that is visibly present but could not be decoded.

    A linear barcode needs roughly 2 pixels per narrow bar to decode, so a
    small or blurred one is seen but not read. Call this only when decoding
    found nothing: it turns a silent non-detection into a message telling
    the operator to move the camera closer or zoom in.

    Returns (left, top, width, height) or None.
    """
    if _presence_detector is None:
        return None
    try:
        ok, corners = _presence_detector.detect(frame_bgr)
    except cv2.error:
        return None
    if not ok or corners is None or len(corners) == 0:
        return None
    return _rect_from_points(corners[0])


def _rect_from_points(points):
    xs = [int(p[0]) for p in points]
    ys = [int(p[1]) for p in points]
    left, top = min(xs), min(ys)
    return left, top, max(xs) - left, max(ys) - top


def _format_name(fmt):
    return getattr(fmt, "name", None) or str(fmt)


def _make_zxing(zxingcpp):
    def decode(frame_bgr):
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        detections = []
        for result in zxingcpp.read_barcodes(gray):
            text = result.text.strip()
            if not text:
                continue
            pos = result.position
            corners = [
                (pos.top_left.x, pos.top_left.y),
                (pos.top_right.x, pos.top_right.y),
                (pos.bottom_right.x, pos.bottom_right.y),
                (pos.bottom_left.x, pos.bottom_left.y),
            ]
            detections.append(Detection(text, _rect_from_points(corners), _format_name(result.format)))
        return detections

    return decode


def _make_pyzbar(zbar_decode):
    def decode(frame_bgr):
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        detections = []
        for obj in zbar_decode(gray):
            try:
                text = obj.data.decode("utf-8").strip()
            except UnicodeDecodeError:
                continue
            if not text:
                continue
            r = obj.rect
            detections.append(Detection(text, (r.left, r.top, r.width, r.height), obj.type))
        return detections

    return decode


def _make_opencv():
    qr_detector = cv2.QRCodeDetector()
    linear_detector = _new_linear_detector()

    def decode(frame_bgr):
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        detections = []

        try:
            ok, texts, points, _ = qr_detector.detectAndDecodeMulti(gray)
        except cv2.error:
            ok, texts, points = False, [], None
        if ok and points is not None:
            for text, quad in zip(texts, points):
                text = (text or "").strip()
                if text:
                    detections.append(Detection(text, _rect_from_points(quad), "QRCODE"))

        if linear_detector is not None:
            try:
                ok, texts, types, corners = linear_detector.detectAndDecodeMulti(gray)
            except cv2.error:
                ok = False
            if ok and corners is not None:
                for text, kind, quad in zip(texts, types, corners):
                    text = (text or "").strip()
                    if text:
                        detections.append(Detection(text, _rect_from_points(quad), kind or "LINEAR"))

        return detections

    return decode


def _select_backend():
    try:
        import zxingcpp

        return "zxing-cpp", _make_zxing(zxingcpp)
    except ImportError:
        pass

    try:
        from pyzbar.pyzbar import decode as zbar_decode

        return "pyzbar", _make_pyzbar(zbar_decode)
    except (ImportError, OSError):
        # pyzbar raises at import time when its ZBar DLL cannot be loaded
        pass

    return "opencv", _make_opencv()


BACKEND_NAME, _decode = _select_backend()


def backend_description() -> str:
    if BACKEND_NAME == "zxing-cpp":
        return "zxing-cpp (QR, DataMatrix, Code128, Code39, EAN/UPC...)"
    if BACKEND_NAME == "pyzbar":
        return "pyzbar (QR, Code128, Code39, EAN/UPC - no DataMatrix)"
    return "OpenCV fallback (QR codes only - run 'pip install zxing-cpp' for more)"


def decode_detections(frame_bgr) -> list[Detection]:
    """Decode every readable code in the image, keeping its position."""
    return _decode(frame_bgr)


def decode_barcodes(frame_bgr) -> list[str]:
    return [d.text for d in decode_detections(frame_bgr)]


def _shift(rect, dx, dy):
    left, top, width, height = rect
    return left + dx, top + dy, width, height


def scan_frame(frame, roi_pixels=None, check_unreadable=True) -> ScanResult:
    """Decode the scan area of a Frame; positions come back in frame pixels.

    roi_pixels is (left, top, width, height) or None to scan the whole frame.
    Only the crop is debayered and decoded, so a small scan area is cheap
    and a thin 1D barcode keeps its full sensor resolution.

    check_unreadable adds the (slower) "is there a barcode I cannot read?"
    probe when nothing decoded.
    """
    if roi_pixels is None:
        roi_pixels = (0, 0, frame.width, frame.height)
    image, (left, top, _w, _h) = frame.crop_bgr(roi_pixels)
    if image is None:
        return ScanResult([], None)

    detections = [
        Detection(d.text, _shift(d.rect, left, top), d.symbology)
        for d in decode_detections(image)
    ]
    unreadable = None
    if not detections and check_unreadable:
        found = locate_unreadable_barcode(image)
        if found is not None:
            unreadable = _shift(found, left, top)
    return ScanResult(detections, unreadable)


class StableBarcodeDetector:
    """Only reports a value once it has read the same code N times in a row.

    Guards against a single misread frame triggering a capture under the
    wrong serial number.
    """

    def __init__(self, required_matches: int = 2):
        self.required_matches = max(1, required_matches)
        self._last_value = None
        self._count = 0
        self.raw_detected = False  # whether the most recent update saw any code at all

    def update(self, frame_bgr):
        return self.update_from_texts(decode_barcodes(frame_bgr))

    def update_from_texts(self, texts):
        """Feed already-decoded values, so the caller can decode just once."""
        value = texts[0] if texts else None
        self.raw_detected = value is not None

        if value is None:
            self._last_value = None
            self._count = 0
            return None

        if value == self._last_value:
            self._count += 1
        else:
            self._last_value = value
            self._count = 1

        return value if self._count >= self.required_matches else None
