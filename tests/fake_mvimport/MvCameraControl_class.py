"""A stand-in for Hikrobot's MvImport wrapper, for tests.

It mirrors the pieces of the real API that hik_camera.py touches - same names,
same ctypes structures, same call shapes (taken from Hikrobot's own Python
samples) - so the real discovery/import/grab code paths run unchanged. It
does NOT prove the real SDK behaves identically; that needs the camera.

Tests steer it through the module-level FAKE object.
"""
import ctypes
import threading
import time
from ctypes import POINTER, Structure, Union, c_float, c_int, c_uint, c_ubyte, c_ushort, c_void_p

MV_USB_DEVICE = 0x00000004
MV_ACCESS_Exclusive = 1
MV_MAX_XML_SYMBOLIC_NUM = 64

BAYER_GB8, BAYER_GB10, BAYER_RG8, RGB8, BGR8 = 0x0108000A, 0x0110000E, 0x01080009, 0x02180014, 0x02180015


class MV_USB3_DEVICE_INFO(Structure):
    _fields_ = [("chModelName", c_ubyte * 64), ("chSerialNumber", c_ubyte * 64)]


class MV_CC_DEVICE_INFO_SPECIAL(Union):
    _fields_ = [("stUsb3VInfo", MV_USB3_DEVICE_INFO)]


class MV_CC_DEVICE_INFO(Structure):
    _fields_ = [("nTLayerType", c_uint), ("SpecialInfo", MV_CC_DEVICE_INFO_SPECIAL)]


class MV_CC_DEVICE_INFO_LIST(Structure):
    _fields_ = [("nDeviceNum", c_uint), ("pDeviceInfo", POINTER(MV_CC_DEVICE_INFO) * 256)]


class MVCC_INTVALUE(Structure):
    _fields_ = [("nCurValue", c_uint), ("nMax", c_uint), ("nMin", c_uint), ("nInc", c_uint)]


class MVCC_FLOATVALUE(Structure):
    _fields_ = [("fCurValue", c_float), ("fMax", c_float), ("fMin", c_float)]


class MVCC_ENUMVALUE(Structure):
    _fields_ = [("nCurValue", c_uint), ("nSupportedNum", c_uint),
                ("nSupportValue", c_uint * MV_MAX_XML_SYMBOLIC_NUM)]


class MV_FRAME_OUT_INFO_EX(Structure):
    _fields_ = [("nWidth", c_ushort), ("nHeight", c_ushort), ("enPixelType", c_int),
                ("nFrameNum", c_uint), ("nFrameLen", c_uint)]


class FakeWorld:
    """The 'hardware'. Reset before each test."""

    def __init__(self):
        self.devices = [("MV-CS200-10UC", "DA0000001")]
        self.width, self.height = 64, 48
        self.supported_pixel_formats = [BAYER_GB8]
        self.pixel_format = BAYER_GB8
        self.open_ret = 0
        self.frames_before_dead = None   # after N frames every grab fails
        self.float_nodes = {"ExposureTime": 8000.0, "Gain": 2.5, "AcquisitionFrameRate": 19.0}
        self.float_limits = {"AcquisitionFrameRate": (1.0, 19.0)}
        self.reject_nodes = set()        # set*() on these names fails
        self.calls = []                  # (method, name, value) in order
        self.frames_sent = 0
        self.lock = threading.Lock()

    def payload(self):
        per_pixel = 3 if self.pixel_format in (RGB8, BGR8) else 1
        return self.width * self.height * per_pixel

    def pattern(self):
        """Deterministic image content; tests compare it byte for byte."""
        import numpy as np

        if self.pixel_format in (RGB8, BGR8):
            return (np.arange(self.payload(), dtype=np.uint32) % 251).astype(np.uint8)
        yy, xx = np.mgrid[0:self.height, 0:self.width]
        return ((xx + 2 * yy) % 251).astype(np.uint8).reshape(-1)


FAKE = FakeWorld()


class MvCamera:
    def __init__(self):
        self._handle = c_void_p()
        self.handle = ctypes.pointer(self._handle)
        self.serial = None

    @staticmethod
    def MV_CC_EnumDevices(layer, device_list):
        FAKE.calls.append(("enum", layer, None))
        device_list.nDeviceNum = len(FAKE.devices)
        for i, (model, serial) in enumerate(FAKE.devices):
            info = MV_CC_DEVICE_INFO()
            info.nTLayerType = MV_USB_DEVICE
            usb = info.SpecialInfo.stUsb3VInfo
            for dst, text in ((usb.chModelName, model), (usb.chSerialNumber, serial)):
                raw = text.encode()
                for j, byte in enumerate(raw):
                    dst[j] = byte
            keep_alive.append(info)
            device_list.pDeviceInfo[i] = ctypes.pointer(info)
        return 0

    def MV_CC_CreateHandle(self, info):
        self.serial = bytes(info.SpecialInfo.stUsb3VInfo.chSerialNumber).split(b"\0")[0].decode()
        FAKE.calls.append(("create", self.serial, None))
        return 0

    def MV_CC_OpenDevice(self, access, switchover):
        FAKE.calls.append(("open", access, None))
        return FAKE.open_ret

    def MV_CC_StartGrabbing(self):
        FAKE.calls.append(("start", None, None))
        return 0

    def MV_CC_StopGrabbing(self):
        FAKE.calls.append(("stop", None, None))
        return 0

    def MV_CC_CloseDevice(self):
        FAKE.calls.append(("close", None, None))
        return 0

    def MV_CC_DestroyHandle(self):
        FAKE.calls.append(("destroy", None, None))
        return 0

    # ----- feature nodes -----

    def MV_CC_SetEnumValue(self, name, value):
        FAKE.calls.append(("set_enum", name, value))
        if name in FAKE.reject_nodes:
            return 0x80000106
        if name == "PixelFormat":
            if value not in FAKE.supported_pixel_formats:
                return 0x80000106
            FAKE.pixel_format = value
        return 0

    def MV_CC_GetEnumValue(self, name, out):
        if name != "PixelFormat":
            return 0x80000106
        out.nCurValue = FAKE.pixel_format
        out.nSupportedNum = len(FAKE.supported_pixel_formats)
        for i, code in enumerate(FAKE.supported_pixel_formats):
            out.nSupportValue[i] = code
        return 0

    def MV_CC_SetFloatValue(self, name, value):
        FAKE.calls.append(("set_float", name, value))
        if name in FAKE.reject_nodes:
            return 0x80000106
        FAKE.float_nodes[name] = value
        return 0

    def MV_CC_GetFloatValue(self, name, out):
        if name not in FAKE.float_nodes:
            return 0x80000106
        out.fCurValue = FAKE.float_nodes[name]
        lo, hi = FAKE.float_limits.get(name, (0.0, 0.0))
        out.fMin, out.fMax = lo, hi
        return 0

    def MV_CC_SetBoolValue(self, name, value):
        FAKE.calls.append(("set_bool", name, value))
        return 0x80000106 if name in FAKE.reject_nodes else 0

    def MV_CC_GetIntValue(self, name, out):
        values = {"PayloadSize": FAKE.payload(), "Width": FAKE.width, "Height": FAKE.height}
        if name not in values:
            return 0x80000106
        out.nCurValue = values[name]
        return 0

    # ----- streaming -----

    def MV_CC_GetOneFrameTimeout(self, pData, nDataSize, info, nMsec):
        buffer = pData._obj  # byref(buf) -> the original ctypes array
        with FAKE.lock:
            dead = FAKE.frames_before_dead is not None and FAKE.frames_sent >= FAKE.frames_before_dead
            if not dead:
                FAKE.frames_sent += 1
        if dead:
            time.sleep(0.002)
            return 0x80000007  # MV_E_NODATA
        data = FAKE.pattern()
        ctypes.memmove(ctypes.addressof(buffer), data.ctypes.data, len(data))
        info.nWidth, info.nHeight = FAKE.width, FAKE.height
        info.enPixelType = FAKE.pixel_format
        info.nFrameNum = FAKE.frames_sent
        info.nFrameLen = len(data)
        time.sleep(0.003)
        return 0


keep_alive = []  # enumerated structs must outlive the list that points at them
