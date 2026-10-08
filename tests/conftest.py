import os
import sys
from pathlib import Path

# Qt tests run without a display
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import pytest  # noqa: E402

FAKE_MVIMPORT_DIR = Path(__file__).resolve().parent / "fake_mvimport"


@pytest.fixture
def fake(monkeypatch):
    """Point the SDK loader at the fake wrapper and reset the fake hardware."""
    import hik_camera

    monkeypatch.setenv("HIKROBOT_MVIMPORT_DIR", str(FAKE_MVIMPORT_DIR))
    monkeypatch.setattr(hik_camera, "_sdk", None)
    sys.modules.pop("MvCameraControl_class", None)
    module = hik_camera.load_sdk()
    module.FAKE.__init__()
    module.keep_alive.clear()
    yield module
    sys.modules.pop("MvCameraControl_class", None)
