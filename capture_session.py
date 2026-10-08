"""Saving a captured image to disk under its barcode folder.

Layout:
    {output_dir}/{barcode}/{barcode}_attempt_{N}.jpg   (or .png)
    {output_dir}/{barcode}/log.txt          (one block appended per attempt)

Every capture (including the first) is numbered, so a re-scan of the same
barcode just gets the next attempt number and nothing already on disk is
ever overwritten. The number is shared across formats, so switching between
JPEG and PNG never reuses one.
"""
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2

IMAGE_FORMATS = ("jpg", "png")
PNG_COMPRESSION = 1   # fastest; higher levels barely shrink a 20 MP photo but cost seconds

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                   *(f"LPT{i}" for i in range(1, 10))}


@dataclass(frozen=True)
class SavedCapture:
    folder: Path
    image_path: Path
    attempt: int


def sanitize_sn(raw_sn: str) -> str:
    """Make a decoded barcode value safe to use as a Windows folder name."""
    cleaned = _INVALID_CHARS.sub("_", raw_sn.strip())
    cleaned = cleaned.strip(" .")
    if cleaned.split(".")[0].upper() in _RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned or "UNKNOWN_SN"


def next_attempt_number(folder: Path, clean_sn: str) -> int:
    pattern = re.compile(rf"^{re.escape(clean_sn)}_attempt_(\d+)\.(?:jpg|png)$", re.IGNORECASE)
    numbers = []
    if folder.is_dir():
        for entry in folder.iterdir():
            match = pattern.match(entry.name)
            if match:
                numbers.append(int(match.group(1)))
    return max(numbers, default=0) + 1


def _write_atomic(path: Path, data: bytes) -> None:
    """Write via a temp file so a crash never leaves a half-written .jpg."""
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def encode_image(image_bgr, image_format: str = "jpg", quality: int = 95) -> bytes:
    """Encode to JPEG (lossy, `quality` 1-100) or PNG (lossless, quality unused)."""
    if image_format == "jpg":
        params = [cv2.IMWRITE_JPEG_QUALITY, int(quality)]
    elif image_format == "png":
        params = [cv2.IMWRITE_PNG_COMPRESSION, PNG_COMPRESSION]
    else:
        raise ValueError(f"unknown image format {image_format!r} (use one of {IMAGE_FORMATS})")
    ok, buffer = cv2.imencode(f".{image_format}", image_bgr, params)
    if not ok:
        raise OSError(f"could not encode the image as {image_format.upper()}")
    return buffer.tobytes()


def encode_jpeg(image_bgr, quality: int) -> bytes:
    return encode_image(image_bgr, "jpg", quality)


def save_capture(
    output_dir: str,
    sn: str,
    image_bgr,
    details: dict | None = None,
    jpeg_quality: int = 95,
    when: datetime | None = None,
    image_format: str = "jpg",
) -> SavedCapture:
    """Write `image_bgr` as the next attempt for `sn`.

    `details` is a mapping of extra "Label: value" lines for log.txt (camera,
    exposure, scan area...). Encoding goes through cv2.imencode and the file
    is written with pathlib, because cv2.imwrite silently fails on paths with
    non-ASCII characters on Windows.
    """
    clean_sn = sanitize_sn(sn)
    data = encode_image(image_bgr, image_format, jpeg_quality)   # fail on a bad format before touching disk
    folder = Path(output_dir) / clean_sn
    folder.mkdir(parents=True, exist_ok=True)

    attempt = next_attempt_number(folder, clean_sn)
    image_path = folder / f"{clean_sn}_attempt_{attempt}.{image_format}"
    _write_atomic(image_path, data)

    height, width = image_bgr.shape[:2]
    when = when or datetime.now()
    lines = [
        f"=== Attempt {attempt} - {when.strftime('%Y-%m-%d %H:%M:%S')} ===",
        f"Barcode: {sn}",
        f"Image: {image_path.name} ({width}x{height}, "
        + ("PNG lossless" if image_format == "png" else f"JPEG quality {jpeg_quality}") + ")",
    ]
    for label, value in (details or {}).items():
        lines.append(f"{label}: {value}")
    with open(folder / "log.txt", "a", encoding="utf-8") as log:
        log.write("\n".join(lines) + "\n\n")

    return SavedCapture(folder, image_path, attempt)
