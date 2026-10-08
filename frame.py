"""One camera frame, plus the conversions the app needs from it.

The MV-CS200-10UC delivers a raw 8-bit Bayer mosaic (about 20 MB at full
resolution). Converting all of that to BGR costs ~60 MB and tens of
milliseconds, so the app never does it unless it has to:

  crop_bgr()  debayers only the scan-area crop used for barcode decoding
  preview()   debayers + shrinks for the on-screen view
  bgr()       full-resolution conversion, only when a capture is saved

Frames from the demo camera are already BGR and skip the Bayer step.
"""
import cv2
import numpy as np

# GenICam names the 2x2 block at the top-left of the sensor; OpenCV's 4-letter
# constants use the same convention (its older 2-letter ones are reversed -
# tests/test_frame.py proves this mapping against synthetic mosaics).
BAYER_TO_BGR = {
    "BayerRG8": cv2.COLOR_BayerRGGB2BGR,
    "BayerGR8": cv2.COLOR_BayerGRBG2BGR,
    "BayerGB8": cv2.COLOR_BayerGBRG2BGR,
    "BayerBG8": cv2.COLOR_BayerBGGR2BGR,
}
# Edge-aware demosaicing for the image that gets saved. OpenCV only offers it
# under the older two-letter (reversed-convention) names, so RG maps to BG_EA etc.
# Measured as a small but consistent gain over bilinear (about 7% lower mean
# error on a synthetic fine-detail image) at no extra cost on a 20 MP frame.
BAYER_TO_BGR_EA = {
    "BayerRG8": cv2.COLOR_BayerBG2BGR_EA,
    "BayerGR8": cv2.COLOR_BayerGB2BGR_EA,
    "BayerGB8": cv2.COLOR_BayerGR2BGR_EA,
    "BayerBG8": cv2.COLOR_BayerRG2BGR_EA,
}
MONO = "Mono8"
BGR = "BGR8"


class Frame:
    def __init__(self, data, pixel_format, frame_id=0, timestamp=0.0):
        if pixel_format not in BAYER_TO_BGR and pixel_format not in (MONO, BGR):
            raise ValueError(f"unsupported pixel format: {pixel_format}")
        self.data = data
        self.pixel_format = pixel_format
        self.frame_id = frame_id
        self.timestamp = timestamp

    @property
    def width(self) -> int:
        return self.data.shape[1]

    @property
    def height(self) -> int:
        return self.data.shape[0]

    def _to_bgr(self, data, best=False):
        if self.pixel_format == BGR:
            return data
        if self.pixel_format == MONO:
            return cv2.cvtColor(data, cv2.COLOR_GRAY2BGR)
        table = BAYER_TO_BGR_EA if best else BAYER_TO_BGR
        return cv2.cvtColor(data, table[self.pixel_format])

    def bgr(self, best=False):
        """Full-resolution BGR image (large - call only when saving).

        best=True uses edge-aware demosaicing; use it for the image that is
        kept. Preview and barcode reading stay on the plain method.
        """
        return self._to_bgr(self.data, best)

    def crop_bgr(self, rect):
        """Debayer just `rect` = (left, top, width, height).

        Returns (image, actual_rect). The rectangle is snapped outward to even
        pixels so the Bayer colour phase of the crop matches the sensor's, and
        clipped to the frame; add actual_rect's origin to any coordinate found
        in the crop to get frame coordinates.
        """
        left, top, width, height = rect
        # snap outward: floor the origin, ceil the far edge, so the crop always
        # covers what was asked for
        right = min(self.width, (left + width + 1) & ~1)
        bottom = min(self.height, (top + height + 1) & ~1)
        left = max(0, left) & ~1
        top = max(0, top) & ~1
        # an odd-sized frame edge: trim rather than hand OpenCV an odd mosaic
        right -= (right - left) & 1
        bottom -= (bottom - top) & 1
        if right <= left or bottom <= top:
            return None, (0, 0, 0, 0)
        crop = self.data[top:bottom, left:right]
        return self._to_bgr(np.ascontiguousarray(crop)), (left, top, right - left, bottom - top)

    def preview(self, max_dim=1600):
        """BGR image shrunk so its longest edge is <= max_dim.

        Returns (image, scale) where scale = preview_size / frame_size.
        """
        scale = min(1.0, max_dim / max(self.width, self.height))
        bgr = self._to_bgr(self.data)
        if scale < 1.0:
            size = (max(1, int(round(self.width * scale))), max(1, int(round(self.height * scale))))
            bgr = cv2.resize(bgr, size, interpolation=cv2.INTER_AREA)
        return bgr, scale
