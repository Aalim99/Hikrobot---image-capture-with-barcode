"""Hikrobot USB3 Vision camera (MV-CS200-10UC) through the MVS SDK.

The camera is not a UVC webcam, so OpenCV cannot open it. It is driven with
Hikrobot's own SDK: the MVS installer provides MvCameraControl.dll and a
ctypes wrapper called "MvImport". Both are proprietary, so they are not
bundled here - install MVS from hikrobotics.com and this module finds the
wrapper on its own (see find_mvimport()).

Like the rest of the app, nothing here raises when hardware is missing: a
missing SDK, an unplugged camera or a camera busy in another program leaves
the camera in a disconnected state with a readable `error`, and the app
still starts.

The camera streams raw 8-bit Bayer. A background thread keeps only the most
recent frame; the UI and the barcode scanner read it with latest().
"""
import ctypes
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from frame import BGR, MONO, Frame

PROJECT_DIR = Path(__file__).resolve().parent
WRAPPER_MODULE = "MvCameraControl_class"

MAX_GRAB_FAILURES = 5       # consecutive failed grabs before declaring a disconnect
GRAB_TIMEOUT_MS = 1000

# GenICam PFNC pixel-type codes (the SDK's PixelType_Gvsp_*)
PIXEL_TYPES = {
    0x01080001: MONO,         # Mono8
    0x01080008: "BayerGR8",
    0x01080009: "BayerRG8",
    0x0108000A: "BayerGB8",
    0x0108000B: "BayerBG8",
    0x02180014: "RGB8",       # RGB8Packed
    0x02180015: BGR,          # BGR8Packed
}
# what we ask the camera for, best first: raw Bayer is the lightest on USB
PREFERRED_PIXEL_TYPES = (0x0108000A, 0x01080009, 0x01080008, 0x0108000B, 0x02180015, 0x02180014)

# Enum features are set by their GenICam entry name ("Off", "Once", "Continuous"),
# which is the same on every camera. These numeric codes are only a fallback for
# firmware that rejects the by-name call. The order is NOT the same for every
# feature: exposure/gain list Once before Continuous, white balance the reverse.
ENUM_CODES = {
    ("ExposureAuto", "Off"): 0, ("ExposureAuto", "Once"): 1, ("ExposureAuto", "Continuous"): 2,
    ("GainAuto", "Off"): 0, ("GainAuto", "Once"): 1, ("GainAuto", "Continuous"): 2,
    ("BalanceWhiteAuto", "Off"): 0, ("BalanceWhiteAuto", "Continuous"): 1, ("BalanceWhiteAuto", "Once"): 2,
    ("GammaSelector", "User"): 1,
}
# What `list_cameras.py --features` checks for: the features this app uses or
# could use. Which of them a given camera exposes is not documented anywhere
# we could verify, so it is better asked of the camera itself.
FEATURE_PROBES = (
    ("PixelFormat", "enum"), ("ExposureAuto", "enum"), ("ExposureTime", "float"),
    ("GainAuto", "enum"), ("Gain", "float"),
    ("BalanceWhiteAuto", "enum"), ("BalanceRatio", "int"),
    ("GammaEnable", "bool"), ("GammaSelector", "enum"), ("Gamma", "float"),
    ("Sharpness", "int"), ("SharpnessEnable", "bool"),
    ("AcquisitionFrameRate", "float"),
    ("AcquisitionFrameRateEnable", "bool"), ("AcquisitionFrameRateControlEnable", "bool"),
)
WB_MODES = ("camera", "auto", "off")   # camera = leave white balance as the camera has it


class SdkError(Exception):
    """The MVS SDK (MvImport wrapper or runtime DLL) could not be loaded."""


@dataclass(frozen=True)
class DeviceInfo:
    model: str
    serial: str
    interface: str = "USB3"

    @property
    def label(self) -> str:
        return f"{self.model}  ({self.serial})"


@dataclass(frozen=True)
class CameraSettings:
    serial: str = ""
    exposure_auto: bool = True
    exposure_us: float = 10000.0
    gain_auto: bool = False
    gain_db: float = 0.0
    fps: float = 10.0
    white_balance: str = "camera"      # "camera" (don't touch) | "auto" | "off"
    gamma_override: bool = False       # False = leave gamma as the camera has it
    gamma: float = 1.0

    @classmethod
    def from_dict(cls, settings: dict) -> "CameraSettings":
        return cls(
            serial=str(settings.get("camera_serial", "")).strip(),
            exposure_auto=bool(settings.get("exposure_auto", True)),
            exposure_us=float(settings.get("exposure_us", 10000.0)),
            gain_auto=bool(settings.get("gain_auto", False)),
            gain_db=float(settings.get("gain_db", 0.0)),
            fps=float(settings.get("acquisition_fps", 10.0)),
            white_balance=str(settings.get("white_balance", "camera")),
            gamma_override=bool(settings.get("gamma_override", False)),
            gamma=float(settings.get("gamma", 1.0)),
        )


# ---------- locating and loading the SDK ----------

def _program_dirs():
    for var in ("ProgramFiles(x86)", "ProgramFiles", "ProgramW6432"):
        value = os.environ.get(var)
        if value:
            yield Path(value)
    if os.name == "nt":
        yield Path(r"C:\Program Files (x86)")
        yield Path(r"C:\Program Files")


def mvimport_candidates():
    """Folders that may hold MvCameraControl_class.py, most specific first."""
    override = os.environ.get("HIKROBOT_MVIMPORT_DIR")
    if override:
        yield Path(override)
    yield PROJECT_DIR / "MvImport"
    for base in _program_dirs():
        yield base / "MVS" / "Development" / "Samples" / "Python" / "MvImport"
    runenv = os.environ.get("MVCAM_COMMON_RUNENV")  # set by the MVS installer on Linux
    if runenv:
        yield Path(runenv).parent / "Samples" / "64" / "Python" / "MvImport"
    yield Path("/opt/MVS/Samples/64/Python/MvImport")


def find_mvimport():
    for folder in mvimport_candidates():
        if (folder / f"{WRAPPER_MODULE}.py").is_file():
            return folder
    return None


def _runtime_dirs():
    """Folders holding MvCameraControl.dll."""
    runenv = os.environ.get("MVCAM_COMMON_RUNENV")
    if runenv:
        yield Path(runenv) / "Win64_x64"
        yield Path(runenv)
    for base in _program_dirs():
        yield base / "Common Files" / "MVS" / "Runtime" / "Win64_x64"


_dll_dir_handles = []  # keep add_dll_directory() registrations alive
_sdk = None


def _register_dll_dirs():
    # Python 3.8+ no longer searches PATH for a DLL's location, so the
    # runtime folder has to be registered explicitly.
    if not hasattr(os, "add_dll_directory"):
        return
    for folder in _runtime_dirs():
        if folder.is_dir():
            try:
                _dll_dir_handles.append(os.add_dll_directory(str(folder)))
            except OSError:
                pass


def load_sdk():
    """Import the MvImport wrapper and return its module. Raises SdkError."""
    global _sdk
    if _sdk is not None:
        return _sdk

    folder = find_mvimport()
    if folder is None:
        raise SdkError(
            "Hikrobot MVS SDK not found. Install Hikrobot's MVS software (with its development "
            "samples, i.e. the folder MVS\\Development\\Samples\\Python\\MvImport), or copy that "
            "MvImport folder next to this app, or set HIKROBOT_MVIMPORT_DIR."
        )
    added = str(folder) not in sys.path
    if added:
        sys.path.append(str(folder))
    _register_dll_dirs()
    try:
        _sdk = __import__(WRAPPER_MODULE)
    except Exception as exc:  # ImportError, OSError (missing DLL), 32/64-bit mismatch...
        sys.modules.pop(WRAPPER_MODULE, None)
        if added:  # don't let a broken wrapper shadow a fixed install on the next try
            sys.path.remove(str(folder))
        raise SdkError(
            f"Found MvImport in '{folder}' but could not load the MVS runtime: {exc}. "
            "Check that MVS is installed and that Python is 64-bit."
        ) from exc
    return _sdk


def sdk_status():
    """(ok, message) for the UI and list_cameras."""
    try:
        load_sdk()
    except SdkError as exc:
        return False, str(exc)
    return True, f"MVS SDK loaded from {find_mvimport()}"


def _c_string(chars) -> str:
    raw = bytes(chars)
    return raw.split(b"\0", 1)[0].decode("utf-8", errors="replace").strip()


def _hex(code: int) -> str:
    return f"0x{code & 0xFFFFFFFF:08X}"


def _enumerate(sdk):
    """(device_list, [DeviceInfo...]). The list owns the native structs."""
    device_list = sdk.MV_CC_DEVICE_INFO_LIST()
    ret = sdk.MvCamera.MV_CC_EnumDevices(sdk.MV_USB_DEVICE, device_list)
    if ret != 0:
        raise SdkError(f"Enumerating cameras failed ({_hex(ret)})")
    devices = []
    for i in range(device_list.nDeviceNum):
        info = ctypes.cast(device_list.pDeviceInfo[i], ctypes.POINTER(sdk.MV_CC_DEVICE_INFO)).contents
        usb = info.SpecialInfo.stUsb3VInfo
        devices.append(DeviceInfo(_c_string(usb.chModelName), _c_string(usb.chSerialNumber)))
    return device_list, devices


def list_devices():
    """Every USB3 Vision camera the SDK can see. Raises SdkError."""
    return _enumerate(load_sdk())[1]


# ---------- the camera ----------

class HikCamera:
    def __init__(self, settings: CameraSettings | None = None):
        self.settings = settings or CameraSettings()
        self.connected = False
        self.error = None
        self.model = ""
        self.serial = ""
        self.pixel_format = ""
        self.actual_width = None
        self.actual_height = None
        self._open_warnings = []    # problems found while opening (non-fatal)
        self._apply_warnings = []   # camera features the last apply_settings could not set

        self._initial = {}          # feature values as found, so "camera default" can be restored
        self._touched = set()       # features this app has changed since then

        self._sdk = None
        self._cam = None
        self._buffer = None
        self._payload = 0
        self._frame = None
        self._frame_counter = 0
        self._problem = None  # why the last grab was unusable, for the disconnect message
        self._lock = threading.Lock()
        self._running = False
        self._thread = None

    @property
    def warnings(self) -> list:
        """Camera features that could not be set (non-fatal), for the UI to show."""
        return self._open_warnings + self._apply_warnings

    # ----- connect / disconnect -----

    def open(self) -> bool:
        """Try to open the camera. Returns success; never raises."""
        self.close()
        self._open_warnings = []
        self._apply_warnings = []
        self._initial = {}
        self._touched = set()
        try:
            return self._open()
        except SdkError as exc:
            return self._fail(str(exc))
        except Exception as exc:  # a driver surprise must not stop the app
            self.close()
            return self._fail(f"Unexpected camera error: {exc}")

    def _fail(self, message: str) -> bool:
        self.connected = False
        self.error = message
        return False

    def _check(self, ret: int, what: str):
        if ret != 0:
            raise SdkError(f"{what} failed ({_hex(ret)})")

    def _open(self) -> bool:
        sdk = self._sdk = load_sdk()
        device_list, devices = _enumerate(sdk)
        if not devices:
            return self._fail("No Hikrobot USB camera found - check the cable and a blue USB 3.0 port")

        wanted = self.settings.serial
        index = next((i for i, d in enumerate(devices) if d.serial == wanted), None) if wanted else 0
        if index is None:
            seen = ", ".join(d.serial for d in devices)
            return self._fail(f"Camera {wanted} not found (connected: {seen})")

        info = ctypes.cast(device_list.pDeviceInfo[index], ctypes.POINTER(sdk.MV_CC_DEVICE_INFO)).contents
        self.model, self.serial = devices[index].model, devices[index].serial

        cam = sdk.MvCamera()
        self._cam = cam
        self._check(cam.MV_CC_CreateHandle(info), "Creating the camera handle")
        ret = cam.MV_CC_OpenDevice(sdk.MV_ACCESS_Exclusive, 0)
        if ret != 0:
            raise SdkError(
                f"Could not open the camera ({_hex(ret)}). Close MVS or any other program "
                "using it, then unplug and re-plug the USB cable."
            )

        self._set_enum("TriggerMode", 0)  # free-run
        self._choose_pixel_format()
        self._snapshot_defaults()
        self.apply_settings(self.settings)

        size = sdk.MVCC_INTVALUE()
        ctypes.memset(ctypes.byref(size), 0, ctypes.sizeof(size))
        self._check(cam.MV_CC_GetIntValue("PayloadSize", size), "Reading the frame size")
        self._payload = int(size.nCurValue)
        self._buffer = (ctypes.c_ubyte * self._payload)()
        self.actual_width = self._get_int("Width")
        self.actual_height = self._get_int("Height")

        self._check(cam.MV_CC_StartGrabbing(), "Starting the stream")
        self.connected = True
        self.error = None
        self._running = True
        self._thread = threading.Thread(target=self._grab_loop, name="hik-grab", daemon=True)
        self._thread.start()
        return True

    def reconfigure(self, settings: CameraSettings) -> bool:
        self.settings = settings
        return self.open()

    def close(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=GRAB_TIMEOUT_MS / 1000 + 1.5)
            self._thread = None
        cam, self._cam = self._cam, None
        if cam is not None:
            for step in (cam.MV_CC_StopGrabbing, cam.MV_CC_CloseDevice, cam.MV_CC_DestroyHandle):
                try:
                    step()
                except Exception:
                    pass
        self._buffer = None
        with self._lock:
            self._frame = None
        self.connected = False

    # ----- camera features -----

    def _set_enum(self, name, value) -> bool:
        return self._cam.MV_CC_SetEnumValue(name, value) == 0

    def _set_symbolic(self, name, symbol) -> bool:
        """Set an enum feature by entry name, falling back to its numeric code."""
        if self._cam.MV_CC_SetEnumValueByString(name, symbol) == 0:
            return True
        code = ENUM_CODES.get((name, symbol))
        return code is not None and self._set_enum(name, code)

    def _get_enum(self, name):
        value = self._sdk.MVCC_ENUMVALUE()
        ctypes.memset(ctypes.byref(value), 0, ctypes.sizeof(value))
        if self._cam.MV_CC_GetEnumValue(name, value) != 0:
            return None
        return int(value.nCurValue)

    def _get_bool(self, name):
        flag = ctypes.c_bool()
        # the wrapper wants either the object or a reference to it, depending on SDK version
        for argument in (flag, ctypes.byref(flag)):
            try:
                ret = self._cam.MV_CC_GetBoolValue(name, argument)
            except (TypeError, ctypes.ArgumentError):
                continue
            return bool(flag.value) if ret == 0 else None
        return None

    def _set_float(self, name, value) -> bool:
        return self._cam.MV_CC_SetFloatValue(name, float(value)) == 0

    def _get_int(self, name):
        value = self._sdk.MVCC_INTVALUE()
        ctypes.memset(ctypes.byref(value), 0, ctypes.sizeof(value))
        if self._cam.MV_CC_GetIntValue(name, value) != 0:
            return None
        return int(value.nCurValue)

    def _get_float(self, name):
        value = self._sdk.MVCC_FLOATVALUE()
        ctypes.memset(ctypes.byref(value), 0, ctypes.sizeof(value))
        if self._cam.MV_CC_GetFloatValue(name, value) != 0:
            return None
        return value

    def _warn(self, message):
        if message not in self._open_warnings:
            self._open_warnings.append(message)

    def _warn_setting(self, message):
        if message not in self._apply_warnings:
            self._apply_warnings.append(message)

    def _snapshot_defaults(self):
        """Remember how the camera was set up, before this app changes anything."""
        self._initial = {
            "BalanceWhiteAuto": self._get_enum("BalanceWhiteAuto"),
            "GammaSelector": self._get_enum("GammaSelector"),
            "GammaEnable": self._get_bool("GammaEnable"),
            "Gamma": getattr(self._get_float("Gamma"), "fCurValue", None),
        }

    def _choose_pixel_format(self):
        """Prefer 8-bit raw Bayer: 20 MB/frame instead of 60 MB over USB."""
        value = self._sdk.MVCC_ENUMVALUE()
        ctypes.memset(ctypes.byref(value), 0, ctypes.sizeof(value))
        if self._cam.MV_CC_GetEnumValue("PixelFormat", value) != 0:
            self._warn("could not read PixelFormat")
            return
        supported = {int(value.nSupportValue[i]) for i in range(value.nSupportedNum)}
        current = int(value.nCurValue)
        for code in PREFERRED_PIXEL_TYPES:
            if code in supported or (not supported and code == current):
                if code != current and not self._set_enum("PixelFormat", code):
                    continue
                self.pixel_format = PIXEL_TYPES[code]
                return
        self._warn(f"no 8-bit Bayer/BGR pixel format offered (current 0x{current:08X})")

    def apply_settings(self, settings: CameraSettings):
        """Push exposure / gain / white balance / gamma / frame-rate to the camera.

        Safe while streaming. Anything the camera refuses is listed in
        `warnings` rather than raised.
        """
        self.settings = settings
        if self._cam is None:
            return
        self._apply_warnings = []

        self._set_symbolic("ExposureAuto", "Continuous" if settings.exposure_auto else "Off")
        if not settings.exposure_auto and not self._set_float("ExposureTime", settings.exposure_us):
            self._warn_setting("could not set ExposureTime")

        self._set_symbolic("GainAuto", "Continuous" if settings.gain_auto else "Off")
        if not settings.gain_auto and not self._set_float("Gain", settings.gain_db):
            self._warn_setting("could not set Gain")

        self._apply_white_balance(settings.white_balance)
        self._apply_gamma(settings)

        fps = settings.fps
        limits = self._get_float("AcquisitionFrameRate")
        if limits is not None and limits.fMax > 0:
            fps = min(max(fps, limits.fMin), limits.fMax)
        # the enable node is called one of two things depending on firmware
        enabled = any(
            self._cam.MV_CC_SetBoolValue(node, True) == 0
            for node in ("AcquisitionFrameRateEnable", "AcquisitionFrameRateControlEnable")
        )
        if not (enabled and self._set_float("AcquisitionFrameRate", fps)):
            self._warn_setting("could not cap the frame rate")

    def _apply_white_balance(self, mode):
        if mode == "camera":
            # give the camera back what it had, if (and only if) we changed it
            initial = self._initial.get("BalanceWhiteAuto")
            if "BalanceWhiteAuto" in self._touched and initial is not None:
                self._set_enum("BalanceWhiteAuto", initial)
                self._touched.discard("BalanceWhiteAuto")
            return
        symbol = "Continuous" if mode == "auto" else "Off"
        if self._set_symbolic("BalanceWhiteAuto", symbol):
            self._touched.add("BalanceWhiteAuto")
        else:
            self._warn_setting("this camera did not accept the white balance setting")

    def _apply_gamma(self, settings):
        if not settings.gamma_override:
            if self._touched & {"Gamma", "GammaEnable", "GammaSelector"}:
                self._restore_gamma()
            return
        # GammaEnable / GammaSelector do not exist on every firmware, so their
        # failure is not reported; only the Gamma value itself matters.
        if self._cam.MV_CC_SetBoolValue("GammaEnable", True) == 0:
            self._touched.add("GammaEnable")
        if self._set_symbolic("GammaSelector", "User"):
            self._touched.add("GammaSelector")
        value = settings.gamma
        limits = self._get_float("Gamma")
        if limits is not None and limits.fMax > 0:
            value = min(max(value, limits.fMin), limits.fMax)
        if self._set_float("Gamma", value):
            self._touched.add("Gamma")
        else:
            self._warn_setting("could not set Gamma")

    def _restore_gamma(self):
        initial = self._initial
        if "GammaSelector" in self._touched and initial.get("GammaSelector") is not None:
            self._set_enum("GammaSelector", initial["GammaSelector"])
        if "Gamma" in self._touched and initial.get("Gamma") is not None:
            self._set_float("Gamma", initial["Gamma"])
        if "GammaEnable" in self._touched and initial.get("GammaEnable") is not None:
            self._cam.MV_CC_SetBoolValue("GammaEnable", initial["GammaEnable"])
        self._touched -= {"Gamma", "GammaEnable", "GammaSelector"}

    def balance_once(self) -> bool:
        """Measure white balance once, from whatever the camera is looking at.

        Point the camera at a white or grey sheet first. Returns False if the
        camera is not connected or does not support it.
        """
        if self._cam is None or not self.connected:
            return False
        if not self._set_symbolic("BalanceWhiteAuto", "Once"):
            self._warn_setting("this camera did not accept 'balance white once'")
            return False
        self._touched.add("BalanceWhiteAuto")
        return True

    # ----- grabbing -----

    def _grab_loop(self):
        # pinned locally: close() clears self._buffer once it has stopped us
        sdk, cam, buffer, payload = self._sdk, self._cam, self._buffer, self._payload
        info = sdk.MV_FRAME_OUT_INFO_EX()
        failures = 0
        while self._running:
            ctypes.memset(ctypes.byref(info), 0, ctypes.sizeof(info))
            ret = cam.MV_CC_GetOneFrameTimeout(ctypes.byref(buffer), payload, info, GRAB_TIMEOUT_MS)
            if ret == 0:
                frame = self._decode_frame(info, buffer, payload)
                if frame is None:
                    failures += 1
                else:
                    failures = 0
                    self._problem = None
                    with self._lock:
                        self._frame = frame
            else:
                failures += 1
                self._problem = None
                time.sleep(0.01)

            if failures >= MAX_GRAB_FAILURES:
                self.connected = False
                self.error = self._problem or "Camera stopped responding (unplugged, or the USB link dropped)"
                self._running = False

    def _decode_frame(self, info, buffer, payload):
        pixel_format = PIXEL_TYPES.get(int(info.enPixelType))
        if pixel_format is None:
            self._problem = f"Unsupported pixel format 0x{int(info.enPixelType):08X}"
            return None
        width, height = int(info.nWidth), int(info.nHeight)
        channels = 3 if pixel_format in (BGR, "RGB8") else 1
        count = width * height * channels
        if width <= 0 or height <= 0 or count > payload or int(info.nFrameLen) < count:
            return None  # truncated transfer

        data = np.frombuffer(buffer, dtype=np.uint8, count=count).copy()
        data = data.reshape((height, width, 3) if channels == 3 else (height, width))
        if pixel_format == "RGB8":
            data, pixel_format = data[:, :, ::-1].copy(), BGR

        self.pixel_format = pixel_format
        self.actual_width, self.actual_height = width, height
        self._frame_counter += 1
        return Frame(data, pixel_format, self._frame_counter, time.time())

    def latest(self):
        """The most recent Frame, or None before the first one arrives."""
        with self._lock:
            return self._frame

    # ----- description for the UI and log.txt -----

    @property
    def title(self) -> str:
        return f"{self.model} · {self.serial}" if self.model else "Hikrobot camera"

    def describe(self) -> str:
        if not self.connected:
            return "not connected"
        return f"{self.model} ({self.serial}), {self.actual_width}x{self.actual_height} {self.pixel_format}"

    def probe_features(self) -> list:
        """[(name, kind, supported, detail)] for each of FEATURE_PROBES."""
        results = []
        for name, kind in FEATURE_PROBES:
            detail = None
            if self._cam is not None:
                if kind == "enum":
                    value = self._get_enum(name)
                    if value is not None:
                        detail = PIXEL_TYPES.get(value, f"0x{value:08X}") if name == "PixelFormat" else f"value {value}"
                elif kind == "float":
                    value = self._get_float(name)
                    if value is not None:
                        detail = f"{value.fCurValue:g}"
                        if value.fMax > value.fMin:
                            detail += f" (range {value.fMin:g} to {value.fMax:g})"
                elif kind == "int":
                    value = self._get_int(name)
                    detail = None if value is None else str(value)
                else:
                    value = self._get_bool(name)
                    detail = None if value is None else ("on" if value else "off")
            results.append((name, kind, detail is not None, detail or "not found on this camera"))
        return results

    def details(self) -> dict:
        """Label -> value lines for log.txt, with live exposure/gain read back."""
        lines = {"Camera": f"{self.model} ({self.serial})",
                 "Pixel format": self.pixel_format or "unknown"}
        if self._cam is not None and self.connected:
            exposure, gain = self._get_float("ExposureTime"), self._get_float("Gain")
            if exposure is not None:
                lines["Exposure"] = f"{exposure.fCurValue:.0f} us"
            if gain is not None:
                lines["Gain"] = f"{gain.fCurValue:.1f} dB"
            gamma = self._get_float("Gamma")
            if gamma is not None:
                lines["Gamma"] = f"{gamma.fCurValue:.2f}"
        lines["White balance"] = {"camera": "camera default", "auto": "auto (continuous)",
                                  "off": "off"}.get(self.settings.white_balance, self.settings.white_balance)
        return lines
