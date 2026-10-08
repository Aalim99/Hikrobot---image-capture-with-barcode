import pytest

import demo_source
from barcode_scanner import scan_frame
from demo_source import DEMO_ITEMS, DemoCamera
from roi import Roi, to_pixels


@pytest.fixture(scope="module")
def camera():
    return DemoCamera()


def show(camera, monkeypatch, serial):
    """Make the demo camera show `serial` right now."""
    monkeypatch.setattr(camera, "_current_serial", lambda: serial)
    return camera.latest()


@pytest.mark.parametrize("serial", list(DEMO_ITEMS))
def test_every_demo_item_decodes_inside_a_box_around_its_label(camera, monkeypatch, serial):
    frame = show(camera, monkeypatch, serial)
    cx, cy = DEMO_ITEMS[serial]
    box = to_pixels(Roi(cx - 0.25, cy - 0.2, 0.5, 0.4), frame.width, frame.height)

    assert [d.text for d in scan_frame(frame, box).detections] == [serial]


def test_a_box_in_the_wrong_place_reads_nothing(camera, monkeypatch):
    frame = show(camera, monkeypatch, "SN-DEMO-0001")   # label is upper right
    lower_left = to_pixels(Roi(0.02, 0.7, 0.2, 0.25), frame.width, frame.height)
    assert scan_frame(frame, lower_left).detections == []


def test_labels_sit_in_different_places():
    assert len(set(DEMO_ITEMS.values())) == len(DEMO_ITEMS)


def test_gap_between_items_shows_an_empty_scene(camera, monkeypatch):
    frame = show(camera, monkeypatch, None)
    assert scan_frame(frame, None, check_unreadable=False).detections == []


def test_cycle_gives_each_item_then_a_gap(monkeypatch, camera):
    seen = []
    for t in (1.0, demo_source.CYCLE + 1.0, 2 * demo_source.CYCLE + 1.0,
              demo_source.ITEM_VISIBLE_SECONDS + 0.5):
        monkeypatch.setattr(demo_source.time, "time", lambda t=t: camera._start + t)
        seen.append(camera._current_serial())
    assert seen == list(DEMO_ITEMS) + [None]


def test_frame_ids_advance_over_time(camera):
    first = camera.latest().frame_id
    import time
    time.sleep(0.25)
    assert camera.latest().frame_id > first


def test_demo_camera_matches_the_real_camera_interface():
    from hik_camera import CameraSettings, HikCamera

    for name in ("connected", "error", "warnings", "title", "latest", "describe", "details",
                 "open", "reconfigure", "apply_settings", "close"):
        assert hasattr(DemoCamera, name) or name in vars(DemoCamera()), name
        assert hasattr(HikCamera, name) or name in vars(HikCamera()), name

    cam = DemoCamera(640, 480)
    assert cam.open() and cam.reconfigure(CameraSettings()) and cam.connected
    cam.apply_settings(CameraSettings())
    cam.close()
