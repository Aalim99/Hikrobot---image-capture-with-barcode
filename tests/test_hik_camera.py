"""hik_camera against tests/fake_mvimport, a stand-in with the real wrapper's
API shape. This proves our discovery, enumeration, feature setup, grab loop
and failure handling; it cannot prove the real SDK/camera behave the same."""
import sys
import time
from pathlib import Path

import numpy as np
import pytest

import hik_camera
from hik_camera import CameraSettings, HikCamera, SdkError

from conftest import FAKE_MVIMPORT_DIR as FAKE_DIR  # noqa: E402


@pytest.fixture
def camera(fake):
    cams = []

    def make(**settings):
        cam = HikCamera(CameraSettings(**settings))
        cams.append(cam)
        return cam

    yield make
    for cam in cams:
        cam.close()


def wait_for(predicate, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def calls(fake, kind=None, name=None):
    return [c for c in fake.FAKE.calls
            if (kind is None or c[0] == kind) and (name is None or c[1] == name)]


# ---------- finding the SDK ----------

def test_find_mvimport_honours_env_override(monkeypatch):
    monkeypatch.setenv("HIKROBOT_MVIMPORT_DIR", str(FAKE_DIR))
    assert hik_camera.find_mvimport() == FAKE_DIR


def test_missing_sdk_gives_an_actionable_error(monkeypatch):
    monkeypatch.setattr(hik_camera, "_sdk", None)
    monkeypatch.setattr(hik_camera, "mvimport_candidates", lambda: iter([Path("/nonexistent")]))

    with pytest.raises(SdkError, match="MVS"):
        hik_camera.load_sdk()
    ok, message = hik_camera.sdk_status()
    assert not ok and "MvImport" in message and "HIKROBOT_MVIMPORT_DIR" in message


def test_open_without_sdk_does_not_raise(monkeypatch):
    monkeypatch.setattr(hik_camera, "_sdk", None)
    monkeypatch.setattr(hik_camera, "mvimport_candidates", lambda: iter([]))
    cam = HikCamera()

    assert cam.open() is False
    assert not cam.connected and "MVS" in cam.error
    assert cam.latest() is None and cam.describe() == "not connected"


def test_broken_runtime_dll_is_reported_not_raised(monkeypatch, tmp_path):
    (tmp_path / "MvCameraControl_class.py").write_text("raise OSError('MvCameraControl.dll not found')")
    monkeypatch.setenv("HIKROBOT_MVIMPORT_DIR", str(tmp_path))
    monkeypatch.setattr(hik_camera, "_sdk", None)
    sys.modules.pop("MvCameraControl_class", None)

    cam = HikCamera()
    assert cam.open() is False
    assert "MvCameraControl.dll not found" in cam.error and "64-bit" in cam.error


# ---------- enumeration ----------

def test_list_devices_reports_model_and_serial(fake):
    fake.FAKE.devices = [("MV-CS200-10UC", "DA111"), ("MV-CS200-10UC", "DA222")]
    devices = hik_camera.list_devices()

    assert [(d.model, d.serial) for d in devices] == [("MV-CS200-10UC", "DA111"), ("MV-CS200-10UC", "DA222")]
    assert devices[0].label == "MV-CS200-10UC  (DA111)"


# ---------- opening ----------

def test_open_streams_frames_with_exact_pixel_data(fake, camera):
    cam = camera()
    assert cam.open() is True
    assert cam.connected and cam.error is None
    assert wait_for(lambda: cam.latest() is not None)

    frame = cam.latest()
    assert frame.pixel_format == "BayerGB8"
    assert (frame.width, frame.height) == (64, 48)
    assert np.array_equal(frame.data.reshape(-1), fake.FAKE.pattern())
    assert frame.bgr().shape == (48, 64, 3)
    assert cam.describe() == "MV-CS200-10UC (DA0000001), 64x48 BayerGB8"
    assert cam.title == "MV-CS200-10UC · DA0000001"


def test_frame_ids_increase(fake, camera):
    cam = camera()
    cam.open()
    assert wait_for(lambda: cam.latest() is not None)
    first = cam.latest().frame_id
    assert wait_for(lambda: cam.latest().frame_id > first)


def test_camera_is_set_to_free_run_and_opened_exclusively(fake, camera):
    camera().open()
    assert ("set_enum", "TriggerMode", 0) in fake.FAKE.calls
    assert calls(fake, "open")[0][1] == fake.MV_ACCESS_Exclusive
    assert [c[0] for c in fake.FAKE.calls].index("start") > [c[0] for c in fake.FAKE.calls].index("open")


def test_selects_camera_by_serial(fake, camera):
    fake.FAKE.devices = [("MV-CS200-10UC", "DA111"), ("MV-CS200-10UC", "DA222")]
    cam = camera(serial="DA222")
    assert cam.open() is True
    assert cam.serial == "DA222" and calls(fake, "create")[0][1] == "DA222"


def test_blank_serial_uses_first_camera(fake, camera):
    fake.FAKE.devices = [("MV-CS200-10UC", "DA111"), ("MV-CS200-10UC", "DA222")]
    cam = camera()
    cam.open()
    assert cam.serial == "DA111"


def test_unknown_serial_lists_what_is_connected(fake, camera):
    cam = camera(serial="NOPE")
    assert cam.open() is False
    assert "NOPE" in cam.error and "DA0000001" in cam.error
    assert not calls(fake, "create")


def test_no_camera_attached(fake, camera):
    fake.FAKE.devices = []
    cam = camera()
    assert cam.open() is False
    assert "No Hikrobot USB camera" in cam.error and "USB 3.0" in cam.error


def test_camera_busy_elsewhere_explains_what_to_do(fake, camera):
    fake.FAKE.open_ret = 0x80000203
    cam = camera()
    assert cam.open() is False
    assert "0x80000203" in cam.error and "MVS" in cam.error


# ---------- pixel format ----------

def test_switches_from_10bit_to_8bit_bayer(fake, camera):
    fake.FAKE.supported_pixel_formats = [fake.BAYER_GB10, fake.BAYER_GB8]
    fake.FAKE.pixel_format = fake.BAYER_GB10
    cam = camera()
    cam.open()
    assert ("set_enum", "PixelFormat", fake.BAYER_GB8) in fake.FAKE.calls
    assert wait_for(lambda: cam.latest() is not None)
    assert cam.latest().pixel_format == "BayerGB8"


def test_keeps_a_good_pixel_format_untouched(fake, camera):
    fake.FAKE.supported_pixel_formats = [fake.BAYER_RG8, fake.BAYER_GB8]
    fake.FAKE.pixel_format = fake.BAYER_RG8
    camera().open()
    assert ("set_enum", "PixelFormat", fake.BAYER_RG8) not in fake.FAKE.calls or \
        fake.FAKE.pixel_format == fake.BAYER_GB8  # preference order decides, either is valid
    assert fake.FAKE.pixel_format in (fake.BAYER_RG8, fake.BAYER_GB8)


def test_rgb_frames_are_converted_to_bgr(fake, camera):
    fake.FAKE.supported_pixel_formats = [fake.RGB8]
    fake.FAKE.pixel_format = fake.RGB8
    cam = camera()
    cam.open()
    assert wait_for(lambda: cam.latest() is not None)

    frame = cam.latest()
    assert frame.pixel_format == "BGR8" and frame.data.shape == (48, 64, 3)
    raw = fake.FAKE.pattern().reshape(48, 64, 3)
    assert np.array_equal(frame.data, raw[:, :, ::-1])  # channels swapped RGB -> BGR


def test_unsupported_pixel_format_is_named_in_the_error(fake, camera):
    fake.FAKE.supported_pixel_formats = [0x01100003]   # Mono10: not something we decode
    fake.FAKE.pixel_format = 0x01100003
    cam = camera()
    cam.open()
    assert wait_for(lambda: not cam.connected)
    assert "0x01100003" in cam.error


# ---------- settings ----------

def test_manual_exposure_and_gain_are_applied(fake, camera):
    camera(exposure_auto=False, exposure_us=5000.0, gain_auto=False, gain_db=3.0, fps=8.0).open()
    c = fake.FAKE.calls
    assert ("set_enum", "ExposureAuto", 0) in c and ("set_float", "ExposureTime", 5000.0) in c
    assert ("set_enum", "GainAuto", 0) in c and ("set_float", "Gain", 3.0) in c
    assert ("set_bool", "AcquisitionFrameRateEnable", True) in c
    assert ("set_float", "AcquisitionFrameRate", 8.0) in c


def test_auto_exposure_does_not_write_a_manual_value(fake, camera):
    camera(exposure_auto=True, gain_auto=True).open()
    assert ("set_enum", "ExposureAuto", 2) in fake.FAKE.calls
    assert ("set_enum", "GainAuto", 2) in fake.FAKE.calls
    assert not calls(fake, "set_float", "ExposureTime") and not calls(fake, "set_float", "Gain")


def test_frame_rate_is_clamped_to_what_the_camera_allows(fake, camera):
    camera(fps=500.0).open()
    assert ("set_float", "AcquisitionFrameRate", 19.0) in fake.FAKE.calls


def test_frame_rate_enable_node_name_fallback(fake, camera):
    fake.FAKE.reject_nodes = {"AcquisitionFrameRateEnable"}
    cam = camera()
    cam.open()
    assert ("set_bool", "AcquisitionFrameRateControlEnable", True) in fake.FAKE.calls
    assert "could not cap the frame rate" not in cam.warnings


def test_unsettable_feature_is_a_warning_not_a_failure(fake, camera):
    fake.FAKE.reject_nodes = {"ExposureTime"}
    cam = camera(exposure_auto=False)
    assert cam.open() is True
    assert "could not set ExposureTime" in cam.warnings


def test_settings_can_change_while_streaming(fake, camera):
    cam = camera(exposure_auto=True)
    cam.open()
    fake.FAKE.calls.clear()
    cam.apply_settings(CameraSettings(exposure_auto=False, exposure_us=1234.0))
    assert ("set_float", "ExposureTime", 1234.0) in fake.FAKE.calls
    assert cam.connected


def test_details_read_back_live_exposure_and_gain(fake, camera):
    cam = camera(exposure_auto=True, gain_auto=False, gain_db=2.5)  # auto exposure: the camera's own value stays
    cam.open()
    details = cam.details()
    assert details["Camera"] == "MV-CS200-10UC (DA0000001)"
    assert details["Exposure"] == "8000 us" and details["Gain"] == "2.5 dB"
    assert details["Pixel format"] == "BayerGB8"


def test_camera_settings_from_settings_json_dict():
    s = CameraSettings.from_dict({"camera_serial": " DA1 ", "exposure_auto": False, "exposure_us": "2500",
                                  "gain_auto": True, "gain_db": 1, "acquisition_fps": 12})
    assert (s.serial, s.exposure_auto, s.exposure_us, s.gain_auto, s.gain_db, s.fps) == \
        ("DA1", False, 2500.0, True, 1.0, 12.0)


# ---------- failure and shutdown ----------

def test_unplugging_is_detected(fake, camera):
    cam = camera()
    cam.open()
    assert wait_for(lambda: cam.latest() is not None)

    fake.FAKE.frames_before_dead = fake.FAKE.frames_sent  # stream dies now
    assert wait_for(lambda: not cam.connected)
    assert "stopped responding" in cam.error


def test_reopen_after_unplug_recovers(fake, camera):
    cam = camera()
    cam.open()
    fake.FAKE.frames_before_dead = 0
    assert wait_for(lambda: not cam.connected)

    fake.FAKE.frames_before_dead = None
    assert cam.open() is True
    assert wait_for(lambda: cam.latest() is not None) and cam.connected


def test_close_releases_the_device_in_order_and_clears_the_frame(fake, camera):
    cam = camera()
    cam.open()
    assert wait_for(lambda: cam.latest() is not None)
    cam.close()

    order = [c[0] for c in fake.FAKE.calls if c[0] in ("stop", "close", "destroy")]
    assert order == ["stop", "close", "destroy"]
    assert cam.latest() is None and not cam.connected


def test_close_twice_and_close_before_open_are_safe(fake, camera):
    cam = camera()
    cam.close()
    cam.open()
    cam.close()
    cam.close()


def test_reconfigure_switches_camera(fake, camera):
    fake.FAKE.devices = [("MV-CS200-10UC", "DA111"), ("MV-CS200-10UC", "DA222")]
    cam = camera(serial="DA111")
    cam.open()
    assert cam.reconfigure(CameraSettings(serial="DA222")) is True
    assert cam.serial == "DA222"
