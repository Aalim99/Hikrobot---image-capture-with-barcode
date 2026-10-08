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
    assert ("set_enum_str", "ExposureAuto", "Off") in c and ("set_float", "ExposureTime", 5000.0) in c
    assert ("set_enum_str", "GainAuto", "Off") in c and ("set_float", "Gain", 3.0) in c
    assert ("set_bool", "AcquisitionFrameRateEnable", True) in c
    assert ("set_float", "AcquisitionFrameRate", 8.0) in c


def test_auto_exposure_does_not_write_a_manual_value(fake, camera):
    camera(exposure_auto=True, gain_auto=True).open()
    assert ("set_enum_str", "ExposureAuto", "Continuous") in fake.FAKE.calls
    assert ("set_enum_str", "GainAuto", "Continuous") in fake.FAKE.calls
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


# ---------- white balance, gamma, balance-once ----------

def _calls_on(fake, *names):
    return [c for c in fake.FAKE.calls if c[1] in names]


def test_white_balance_modes_are_set_by_entry_name(fake, camera):
    camera(white_balance="auto").open()
    assert ("set_enum_str", "BalanceWhiteAuto", "Continuous") in fake.FAKE.calls
    assert fake.FAKE.enum_state["BalanceWhiteAuto"] == 1

    fake.FAKE.calls.clear()
    camera(white_balance="off").open()
    assert ("set_enum_str", "BalanceWhiteAuto", "Off") in fake.FAKE.calls
    assert fake.FAKE.enum_state["BalanceWhiteAuto"] == 0


def test_camera_default_white_balance_and_gamma_are_not_touched(fake, camera):
    cam = camera()           # white_balance="camera", no gamma override
    cam.open()
    assert _calls_on(fake, "BalanceWhiteAuto", "Gamma", "GammaEnable", "GammaSelector") == []
    assert cam.warnings == []


def test_numeric_fallback_uses_each_features_own_order(fake, camera):
    fake.FAKE.reject_by_string = True            # firmware that refuses the by-name call
    camera(white_balance="auto", exposure_auto=True).open()

    assert ("set_enum", "BalanceWhiteAuto", 1) in fake.FAKE.calls, "white balance: Continuous is 1"
    assert ("set_enum", "ExposureAuto", 2) in fake.FAKE.calls, "exposure: Continuous is 2"
    assert fake.FAKE.enum_state["BalanceWhiteAuto"] == 1 and fake.FAKE.enum_state["ExposureAuto"] == 2


def test_unsupported_white_balance_is_a_warning_not_a_failure(fake, camera):
    fake.FAKE.reject_nodes = {"BalanceWhiteAuto"}
    cam = camera(white_balance="auto")
    assert cam.open() is True
    assert any("white balance" in w for w in cam.warnings)


def test_gamma_override_enables_selects_and_sets_the_value(fake, camera):
    camera(gamma_override=True, gamma=2.2).open()
    calls_ = fake.FAKE.calls
    assert ("set_bool", "GammaEnable", True) in calls_
    assert ("set_enum_str", "GammaSelector", "User") in calls_
    assert ("set_float", "Gamma", 2.2) in calls_
    assert fake.FAKE.float_nodes["Gamma"] == pytest.approx(2.2)


@pytest.mark.parametrize("asked,applied", [(9.0, 4.0), (0.01, 0.1), (1.5, 1.5)])
def test_gamma_is_kept_inside_what_the_camera_allows(fake, camera, asked, applied):
    camera(gamma_override=True, gamma=asked).open()
    assert fake.FAKE.float_nodes["Gamma"] == pytest.approx(applied)


def test_a_rejected_gamma_value_is_a_warning(fake, camera):
    fake.FAKE.reject_nodes = {"Gamma"}
    cam = camera(gamma_override=True, gamma=2.0)
    assert cam.open() is True
    assert "could not set Gamma" in cam.warnings


def test_missing_gamma_enable_and_selector_nodes_are_not_reported(fake, camera):
    fake.FAKE.reject_nodes = {"GammaEnable", "GammaSelector"}   # firmware without those nodes
    cam = camera(gamma_override=True, gamma=2.0)
    cam.open()
    assert cam.warnings == [] and fake.FAKE.float_nodes["Gamma"] == pytest.approx(2.0)


def test_switching_back_to_camera_default_restores_what_the_camera_had(fake, camera):
    fake.FAKE.float_nodes["Gamma"] = 1.8
    fake.FAKE.bool_state["GammaEnable"] = True
    fake.FAKE.enum_state["GammaSelector"] = 2            # sRGB
    fake.FAKE.enum_state["BalanceWhiteAuto"] = 1         # continuous

    cam = camera(white_balance="off", gamma_override=True, gamma=3.0)
    cam.open()
    assert fake.FAKE.enum_state["BalanceWhiteAuto"] == 0
    assert fake.FAKE.float_nodes["Gamma"] == pytest.approx(3.0)
    assert fake.FAKE.enum_state["GammaSelector"] == 1

    cam.apply_settings(CameraSettings())                 # back to "camera default"
    assert fake.FAKE.enum_state["BalanceWhiteAuto"] == 1
    assert fake.FAKE.float_nodes["Gamma"] == pytest.approx(1.8)
    assert fake.FAKE.bool_state["GammaEnable"] is True
    assert fake.FAKE.enum_state["GammaSelector"] == 2


def test_restoring_only_happens_for_features_this_app_changed(fake, camera):
    cam = camera()
    cam.open()
    fake.FAKE.calls.clear()
    cam.apply_settings(CameraSettings())
    assert _calls_on(fake, "BalanceWhiteAuto", "Gamma", "GammaEnable", "GammaSelector") == []


@pytest.mark.parametrize("style", ["instance", "byref"])
def test_reading_a_bool_works_with_either_wrapper_style(fake, camera, style):
    fake.FAKE.bool_getter_style = style
    fake.FAKE.bool_state["GammaEnable"] = True
    cam = camera(gamma_override=True, gamma=2.0)
    cam.open()
    cam.apply_settings(CameraSettings())
    assert fake.FAKE.bool_state["GammaEnable"] is True, "the original (True) must be read and put back"


def test_warnings_from_one_apply_do_not_stick_after_a_good_one(fake, camera):
    fake.FAKE.reject_nodes = {"Gamma"}
    cam = camera(gamma_override=True, gamma=2.0)
    cam.open()
    assert "could not set Gamma" in cam.warnings

    fake.FAKE.reject_nodes = set()
    cam.apply_settings(cam.settings)
    assert cam.warnings == []


def test_balance_once_measures_white_balance_now(fake, camera):
    cam = camera()
    cam.open()
    assert cam.balance_once() is True
    assert ("set_enum_str", "BalanceWhiteAuto", "Once") in fake.FAKE.calls
    assert fake.FAKE.enum_state["BalanceWhiteAuto"] == 2


def test_balance_once_falls_back_to_the_numeric_code(fake, camera):
    fake.FAKE.reject_by_string = True
    cam = camera()
    cam.open()
    assert cam.balance_once() is True
    assert fake.FAKE.enum_state["BalanceWhiteAuto"] == 2


def test_balance_once_reports_failure(fake, camera):
    assert camera().balance_once() is False               # not connected
    fake.FAKE.reject_nodes = {"BalanceWhiteAuto"}
    cam = camera()
    cam.open()
    assert cam.balance_once() is False
    assert any("balance white once" in w for w in cam.warnings)


def test_balance_once_changes_are_given_back_on_camera_default(fake, camera):
    cam = camera()
    cam.open()
    cam.balance_once()
    assert fake.FAKE.enum_state["BalanceWhiteAuto"] == 2
    cam.apply_settings(CameraSettings())
    assert fake.FAKE.enum_state["BalanceWhiteAuto"] == 1   # as found


def test_details_for_the_log_include_gamma_and_white_balance(fake, camera):
    cam = camera(white_balance="auto")
    cam.open()
    details = cam.details()
    assert details["White balance"] == "auto (continuous)"
    assert details["Gamma"] == "1.00"
    assert camera().details()["White balance"] == "camera default"


def test_new_settings_come_from_settings_json_keys():
    s = CameraSettings.from_dict({"white_balance": "auto", "gamma_override": True, "gamma": "2.4"})
    assert (s.white_balance, s.gamma_override, s.gamma) == ("auto", True, 2.4)
    d = CameraSettings.from_dict({})
    assert (d.white_balance, d.gamma_override, d.gamma) == ("camera", False, 1.0)
