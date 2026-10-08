"""Synthetic images shared by the tests."""
import io

import barcode
import cv2
import numpy as np
import qrcode
from barcode.writer import ImageWriter
from PIL import Image

from frame import Frame

# colours of the 2x2 block at the sensor's top-left, per GenICam pixel format
BAYER_BLOCKS = {
    "BayerRG8": [["R", "G"], ["G", "B"]],
    "BayerGR8": [["G", "R"], ["B", "G"]],
    "BayerGB8": [["G", "B"], ["R", "G"]],
    "BayerBG8": [["B", "G"], ["G", "R"]],
}
_CHANNEL = {"R": 2, "G": 1, "B": 0}  # index into a BGR pixel


def mosaic(bgr, pixel_format):
    """What a Bayer sensor would output when looking at `bgr`."""
    h, w = bgr.shape[:2]
    out = np.zeros((h, w), np.uint8)
    for dy in range(2):
        for dx in range(2):
            channel = _CHANNEL[BAYER_BLOCKS[pixel_format][dy][dx]]
            out[dy::2, dx::2] = bgr[dy::2, dx::2, channel]
    return out


def bayer_frame(bgr, pixel_format="BayerRG8", frame_id=1):
    return Frame(mosaic(bgr, pixel_format), pixel_format, frame_id=frame_id, timestamp=1000.0)


def bgr_frame(bgr, frame_id=1):
    return Frame(bgr, "BGR8", frame_id=frame_id, timestamp=1000.0)


def qr_image(text, size=300):
    img = qrcode.make(text).convert("RGB").resize((size, size), Image.NEAREST)
    return np.array(img)[:, :, ::-1].copy()  # RGB -> BGR


def linear_image(kind, value, dpi=600):
    cls = barcode.get_barcode_class(kind)
    buf = io.BytesIO()
    cls(value, writer=ImageWriter()).write(
        buf, options={"module_height": 10.0, "quiet_zone": 4.0, "dpi": dpi}
    )
    buf.seek(0)
    return np.array(Image.open(buf).convert("RGB"))[:, :, ::-1].copy()


def scene(width, height, items, background=70):
    """A grey frame with images pasted at (left, top)."""
    frame = np.full((height, width, 3), background, np.uint8)
    for image, (left, top) in items:
        h, w = image.shape[:2]
        frame[top:top + h, left:left + w] = image
    return frame


def resize_to_width(image, width):
    height = int(image.shape[0] * width / image.shape[1])
    return cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
