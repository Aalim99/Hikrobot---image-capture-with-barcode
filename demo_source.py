"""A fake camera that renders a synthetic scene, for running the app with no
hardware attached (python main.py --demo).

Items cycle through the view with gaps in between, each carrying a barcode in
a *different place*, so the real detect -> countdown -> capture -> item-removed
flow can be exercised end to end - and so you have to draw the scan area in
the right spot, just like with the real camera.

It has the same interface as hik_camera.HikCamera.
"""
import time
from pathlib import Path

import cv2
import numpy as np

from frame import BGR, Frame

ASSETS = Path(__file__).resolve().parent / "assets"

# serial -> where its label sits in the frame, as (centre x, centre y) fractions
DEMO_ITEMS = {
    "SN-DEMO-0001": (0.70, 0.30),
    "SN-DEMO-0002": (0.28, 0.62),
    "SN-DEMO-0003": (0.50, 0.46),   # Code128 label, wider
}
ITEM_VISIBLE_SECONDS = 9.0
GAP_SECONDS = 3.0
CYCLE = ITEM_VISIBLE_SECONDS + GAP_SECONDS
DEMO_FPS = 10

QR_FRACTION = 0.14           # QR label width as a fraction of frame width
LINEAR_FRACTION = 0.30       # Code128 label width (a linear code needs ~2px per bar)

_BOARD = (52, 92, 40)
_SOLDER = (168, 178, 182)


def _render_item(width, height, seed):
    """A dark bench with a green board covered in small components."""
    frame = np.full((height, width, 3), (28, 30, 34), dtype=np.uint8)
    mx, my = int(width * 0.06), int(height * 0.07)
    cv2.rectangle(frame, (mx, my), (width - mx, height - my), _BOARD, -1)
    cv2.rectangle(frame, (mx, my), (width - mx, height - my), (86, 128, 74), max(2, width // 600))

    rng = np.random.default_rng(seed)
    for _ in range(70):
        x = int(rng.integers(mx + 20, width - mx - 120))
        y = int(rng.integers(my + 20, height - my - 80))
        w = int(rng.integers(30, 130))
        h = int(rng.integers(20, 80))
        shade = int(rng.integers(20, 70))
        cv2.rectangle(frame, (x, y), (x + w, y + h), (shade, shade, shade + 8), -1)
        cv2.rectangle(frame, (x, y), (x + w, y + h), _SOLDER, 2)
    return frame


def _label_image(serial, frame_width):
    linear = ASSETS / f"{serial}-code128.png"
    path = linear if linear.exists() else ASSETS / f"{serial}.png"
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    target_w = int(frame_width * (LINEAR_FRACTION if linear.exists() else QR_FRACTION))
    target_h = int(img.shape[0] * target_w / img.shape[1])
    interpolation = cv2.INTER_AREA if target_w < img.shape[1] else cv2.INTER_NEAREST
    return cv2.cvtColor(cv2.resize(img, (target_w, target_h), interpolation=interpolation),
                        cv2.COLOR_GRAY2BGR)


class DemoCamera:
    def __init__(self, width: int = 2736, height: int = 1824):
        self.width = width
        self.height = height
        self.actual_width = width
        self.actual_height = height
        self.connected = True
        self.error = None
        self.warnings = []
        self.model = "Demo camera"
        self.serial = "DEMO"
        self.pixel_format = BGR

        self._scenes = {}
        for index, (serial, (cx, cy)) in enumerate(DEMO_ITEMS.items()):
            scene = _render_item(width, height, seed=index * 17)
            label = _label_image(serial, width)
            if label is not None:
                h, w = label.shape[:2]
                x, y = int(cx * width - w / 2), int(cy * height - h / 2)
                pad = max(8, width // 300)
                cv2.rectangle(scene, (x - pad, y - pad), (x + w + pad, y + h + pad), (255, 255, 255), -1)
                scene[y:y + h, x:x + w] = label
            self._scenes[serial] = scene

        self._empty = np.full((height, width, 3), (24, 26, 30), dtype=np.uint8)
        cv2.putText(self._empty, "DEMO - nothing in view", (int(width * 0.30), height // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, width / 1400, (90, 96, 110), max(2, width // 900))
        self._start = time.time()

    def _current_serial(self):
        elapsed = time.time() - self._start
        if elapsed % CYCLE > ITEM_VISIBLE_SECONDS:
            return None
        return list(DEMO_ITEMS)[int(elapsed // CYCLE) % len(DEMO_ITEMS)]

    def latest(self):
        now = time.time()
        serial = self._current_serial()
        data = self._empty if serial is None else self._scenes[serial]
        return Frame(data, BGR, frame_id=int(now * DEMO_FPS), timestamp=now)

    @property
    def title(self) -> str:
        return "Demo camera (synthetic)"

    def describe(self) -> str:
        return f"demo camera, {self.width}x{self.height}"

    def details(self) -> dict:
        return {"Camera": "Demo camera (synthetic)", "Pixel format": BGR}

    def open(self) -> bool:
        return True

    def reconfigure(self, settings) -> bool:
        return True

    def apply_settings(self, settings):
        pass

    def balance_once(self) -> bool:
        return True

    def close(self):
        pass
