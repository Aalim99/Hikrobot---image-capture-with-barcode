"""Entry point for the barcode image capture app.

Starts the window even when no camera is attached, so the app can be set up
before the hardware is connected. Run with --demo to drive the whole workflow
with a synthetic camera.
"""
import argparse
import sys

INSTALL_HINT = (
    "Install the dependencies first, from this folder:\n\n"
    "    pip install -r requirements.txt"
)


def _fatal(title, message):
    print(f"\n{title}\n{'-' * len(title)}\n{message}\n", file=sys.stderr)
    sys.exit(1)


def _check_dependencies():
    for module, name in (("PySide6", "PySide6 (Qt)"), ("cv2", "OpenCV"), ("numpy", "NumPy")):
        try:
            __import__(module)
        except ImportError as exc:
            _fatal(f"{name} is not installed", f"Could not import {module}: {exc}\n\n{INSTALL_HINT}")


def main():
    parser = argparse.ArgumentParser(description="Hikrobot barcode image capture")
    parser.add_argument("--demo", action="store_true",
                        help="run with a synthetic camera so the app works with no hardware attached")
    args = parser.parse_args()

    _check_dependencies()

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    import barcode_scanner
    import config as config_module
    from ui.main_window import MainWindow
    from ui.theme import STYLESHEET, sans

    settings = config_module.load_settings()
    print(f"Barcode decoder: {barcode_scanner.backend_description()}")

    if args.demo:
        from demo_source import DemoCamera

        camera = DemoCamera()
        print("Demo mode: synthetic camera, no hardware used.")
    else:
        from hik_camera import CameraSettings, HikCamera

        camera = HikCamera(CameraSettings.from_dict(settings))
        camera.error = "Connecting to the camera…"

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(sans(10))
    app.setStyleSheet(STYLESHEET)

    window = MainWindow(camera, settings, demo=args.demo)
    window.show()

    if not args.demo:
        def connect():
            if camera.open():
                print(f"Camera: {camera.describe()}")
                for warning in camera.warnings:
                    print(f"  note: {warning}", file=sys.stderr)
                return
            print(f"Camera: {camera.error}", file=sys.stderr)
            print(
                "\nThe app will still open. To fix this:\n"
                "  - run 'python list_cameras.py --grab' to see which step fails\n"
                "  - press F5 in the app to reconnect once it is fixed\n"
                "  - or run 'python main.py --demo' to try the app without the camera\n",
                file=sys.stderr,
            )

        # after the window is on screen, so a slow USB enumeration doesn't look like a hang
        QTimer.singleShot(100, connect)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
