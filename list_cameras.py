"""First-run check for the Hikrobot camera.

    python list_cameras.py                  is the MVS SDK found? which cameras are connected?
    python list_cameras.py --grab           ...and open the camera, grab one frame, save it
    python list_cameras.py --features       ...and list which image settings this camera supports
    python list_cameras.py --grab --features --serial DA1234567 --out test.jpg

Run this before the main app. It prints exactly which step fails (SDK not
installed, no camera on the USB bus, camera busy in MVS, no frames arriving)
instead of the app just showing NO SIGNAL.
"""
import argparse
import sys
import time

import hik_camera


def _step(ok, text):
    print(f"  [{'OK' if ok else 'FAIL'}] {text}")
    return ok


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check the Hikrobot camera setup")
    parser.add_argument("--grab", action="store_true", help="open the camera and save one frame")
    parser.add_argument("--features", action="store_true",
                        help="open the camera and list which white balance / gamma / ... settings it supports")
    parser.add_argument("--serial", default="", help="camera serial number (default: first found)")
    parser.add_argument("--out", default="camera_test.jpg", help="where --grab saves the frame")
    args = parser.parse_args(argv)

    print("1. MVS SDK")
    ok, message = hik_camera.sdk_status()
    if not _step(ok, message):
        return 1

    print("2. Cameras on the USB bus")
    try:
        devices = hik_camera.list_devices()
    except hik_camera.SdkError as exc:
        _step(False, str(exc))
        return 1
    if not _step(bool(devices), f"{len(devices)} found"):
        print("     Check the cable and use a blue USB 3.0 port. Close MVS if it is running.")
        return 1
    for device in devices:
        print(f"     - {device.label}")

    if not (args.grab or args.features):
        print("\nAll good so far. Add --grab to test streaming, --features to see supported settings.")
        return 0

    print("3. Opening the camera")
    camera = hik_camera.HikCamera(hik_camera.CameraSettings(serial=args.serial))
    try:
        if not _step(camera.open(), camera.error or f"opened {camera.describe()}"):
            return 1
        for warning in camera.warnings:
            print(f"     note: {warning}")

        if args.features:
            print("   Image settings this camera supports")
            for name, kind, supported, detail in camera.probe_features():
                print(f"  [{'OK' if supported else '--'}] {name:<34} {kind:<5} {detail}")
            if not args.grab:
                return 0

        print("   Streaming one frame")
        started = time.time()
        frame = None
        while frame is None and time.time() - started < 8:
            frame = camera.latest()
            time.sleep(0.05)
        if not _step(frame is not None, "first frame received" if frame else "no frame within 8 s"):
            print(f"     {camera.error or 'The camera opened but is not delivering images.'}")
            return 1
        print(f"     {frame.width}x{frame.height}, {frame.pixel_format}, "
              f"{time.time() - started:.1f} s to first frame")
        for label, value in camera.details().items():
            print(f"     {label}: {value}")

        from barcode_scanner import backend_description, scan_frame
        from capture_session import encode_jpeg

        with open(args.out, "wb") as handle:
            handle.write(encode_jpeg(frame.bgr(), 92))
        _step(True, f"saved {args.out}")

        found = scan_frame(frame, check_unreadable=False).detections
        print(f"4. Barcodes in that frame ({backend_description()})")
        if found:
            for det in found:
                print(f"     - {det.text}   [{det.symbology}]")
        else:
            print("     none read (fine if nothing was in front of the camera)")
    finally:
        camera.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
