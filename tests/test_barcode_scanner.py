import numpy as np

from barcode_scanner import (
    StableBarcodeDetector,
    decode_barcodes,
    decode_detections,
    scan_frame,
)
from helpers import bayer_frame, bgr_frame, linear_image, qr_image, resize_to_width, scene


def _blank_frame(size=300):
    return np.full((size, size, 3), 255, dtype=np.uint8)


def test_decode_barcodes_reads_qr_code():
    assert decode_barcodes(qr_image("SN-ABC-123")) == ["SN-ABC-123"]


def test_decode_barcodes_empty_on_blank_frame():
    assert decode_barcodes(_blank_frame()) == []


def test_stable_detector_requires_consecutive_matches():
    detector = StableBarcodeDetector(required_matches=2)
    frame = qr_image("SN-XYZ")

    assert detector.update(frame) is None  # 1st read: not yet stable
    assert detector.raw_detected is True
    assert detector.update(frame) == "SN-XYZ"  # 2nd consecutive read: stable


def test_stable_detector_resets_on_blank_frame():
    detector = StableBarcodeDetector(required_matches=2)
    frame = qr_image("SN-RESET")

    detector.update(frame)
    assert detector.update(frame) == "SN-RESET"

    assert detector.update(_blank_frame()) is None
    assert detector.raw_detected is False

    # seeing the same SN again afterwards needs required_matches again
    assert detector.update(frame) is None
    assert detector.update(frame) == "SN-RESET"


def test_decode_detections_reports_position_for_overlay():
    detections = decode_detections(qr_image("SN-POS-1", size=300))

    assert len(detections) == 1
    det = detections[0]
    assert det.text == "SN-POS-1"
    left, top, width, height = det.rect
    assert width > 0 and height > 0
    assert 0 <= left < 300 and 0 <= top < 300
    assert left + width <= 300 and top + height <= 300


def test_update_from_texts_matches_frame_decoding():
    detector = StableBarcodeDetector(required_matches=2)

    assert detector.update_from_texts(["SN-1"]) is None
    assert detector.update_from_texts(["SN-1"]) == "SN-1"
    assert detector.raw_detected is True

    assert detector.update_from_texts([]) is None
    assert detector.raw_detected is False


def test_stable_detector_switches_to_new_value_after_required_matches():
    detector = StableBarcodeDetector(required_matches=2)
    frame_a = qr_image("SN-A")
    frame_b = qr_image("SN-B")

    detector.update(frame_a)
    assert detector.update(frame_a) == "SN-A"

    # a different code seen once resets the streak to 1, not yet stable
    assert detector.update(frame_b) is None
    assert detector.update(frame_b) == "SN-B"


# ---- scanning a scan area inside a big camera frame ----

QR_AT = (1200, 700)  # top-left of the QR in the test scene
QR_SIZE = 300


def _big_scene():
    return scene(2400, 1600, [(qr_image("SN-ROI-77", QR_SIZE), QR_AT)])


def test_scan_frame_finds_barcode_inside_roi_and_reports_frame_coordinates():
    frame = bayer_frame(_big_scene(), "BayerRG8")
    result = scan_frame(frame, roi_pixels=(1100, 600, 500, 500))

    assert [d.text for d in result.detections] == ["SN-ROI-77"]
    left, top, w, h = result.detections[0].rect
    # rect is in FRAME pixels (offset back from the crop): it lies inside the
    # pasted QR image. Crop coordinates would be ~100px smaller and fail this.
    assert QR_AT[0] <= left and left + w <= QR_AT[0] + QR_SIZE
    assert QR_AT[1] <= top and top + h <= QR_AT[1] + QR_SIZE
    assert w > QR_SIZE // 2 and h > QR_SIZE // 2


def test_scan_frame_ignores_barcodes_outside_the_roi():
    frame = bayer_frame(_big_scene(), "BayerRG8")
    result = scan_frame(frame, roi_pixels=(0, 0, 800, 600))

    assert result.detections == []


def test_scan_frame_without_roi_scans_everything():
    frame = bayer_frame(_big_scene(), "BayerGB8")
    result = scan_frame(frame, roi_pixels=None)

    assert [d.text for d in result.detections] == ["SN-ROI-77"]


def test_scan_frame_works_on_bgr_frames_too():
    result = scan_frame(bgr_frame(_big_scene()), roi_pixels=(1100, 600, 500, 500))
    assert [d.text for d in result.detections] == ["SN-ROI-77"]


def test_scan_frame_roi_at_odd_offsets_still_decodes():
    frame = bayer_frame(_big_scene(), "BayerBG8")
    result = scan_frame(frame, roi_pixels=(1111, 611, 487, 483))
    assert [d.text for d in result.detections] == ["SN-ROI-77"]


def test_scan_frame_flags_a_barcode_too_small_to_read():
    bar = resize_to_width(linear_image("code128", "SN-PCB-000123"), 240)
    frame = bgr_frame(scene(1920, 1080, [(bar, (800, 450))]))

    result = scan_frame(frame, roi_pixels=(700, 350, 500, 400))
    assert result.detections == []
    assert result.unreadable_rect is not None
    left, top, _w, _h = result.unreadable_rect
    assert 700 <= left < 1200 and 350 <= top < 750, "hint must be in frame coordinates"


def test_scan_frame_skips_the_unreadable_probe_when_not_asked():
    bar = resize_to_width(linear_image("code128", "SN-PCB-000123"), 240)
    frame = bgr_frame(scene(1920, 1080, [(bar, (800, 450))]))
    assert scan_frame(frame, (700, 350, 500, 400), check_unreadable=False).unreadable_rect is None
