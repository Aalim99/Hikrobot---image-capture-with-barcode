"""Settings load/save for the barcode capture app, backed by settings.json."""
import json
from pathlib import Path

DEFAULT_SETTINGS = {
    "output_dir": str(Path.home() / "Barcode_Captures"),
    "camera_serial": "",          # "" = use the first Hikrobot camera found
    "exposure_auto": True,
    "exposure_us": 10000.0,       # used when exposure_auto is off
    "gain_auto": False,
    "gain_db": 0.0,               # used when gain_auto is off
    "acquisition_fps": 10.0,      # cap; full-res 8-bit Bayer is ~20 MB/frame
    "capture_delay_seconds": 1.5,
    "barcode_lost_reset_seconds": 1.5,
    "jpeg_quality": 95,
    "barcode_stable_reads": 2,
    "roi": None,                  # scan area [x, y, w, h] as 0..1 fractions, or null = whole frame
}

SETTINGS_PATH = Path(__file__).resolve().parent / "settings.json"


def load_settings() -> dict:
    data = {}
    if SETTINGS_PATH.exists():
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        if not isinstance(data, dict):
            data = {}
    merged = {**DEFAULT_SETTINGS, **data}
    if merged != data:  # don't touch the file when nothing actually changed
        save_settings(merged)
    return merged


def save_settings(settings: dict) -> None:
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")
