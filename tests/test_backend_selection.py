"""The decoder must degrade gracefully. A missing or unloadable backend
(e.g. pyzbar without the VC++ 2013 runtime on Windows) must never stop the
app from starting - it falls through to the next backend instead.
"""
import builtins
import importlib

import numpy as np
import pytest
import qrcode

import barcode_scanner


def _qr_frame(text, size=300):
    img = qrcode.make(text).convert("RGB").resize((size, size))
    return np.array(img)[:, :, ::-1].copy()


def _reload_with_blocked(monkeypatch, blocked):
    """Reimport barcode_scanner with some backend imports forced to fail."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in blocked:
            raise blocked[name]
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    return importlib.reload(barcode_scanner)


@pytest.fixture(autouse=True)
def _restore_module():
    yield
    importlib.reload(barcode_scanner)


def test_selected_backend_is_known_and_decodes():
    assert barcode_scanner.BACKEND_NAME in barcode_scanner.KNOWN_BACKENDS
    assert barcode_scanner.decode_barcodes(_qr_frame("SN-BACKEND")) == ["SN-BACKEND"]


def test_falls_back_to_opencv_when_other_backends_missing(monkeypatch):
    mod = _reload_with_blocked(
        monkeypatch,
        {
            "zxingcpp": ImportError("no zxingcpp"),
            "pyzbar.pyzbar": OSError("Could not find module 'libzbar-64.dll'"),
        },
    )
    assert mod.BACKEND_NAME == "opencv"
    assert mod.decode_barcodes(_qr_frame("SN-FALLBACK")) == ["SN-FALLBACK"]


def test_pyzbar_dll_failure_does_not_raise_at_import(monkeypatch):
    """The exact Windows failure mode: pyzbar installed, DLL unloadable."""
    mod = _reload_with_blocked(
        monkeypatch,
        {"pyzbar.pyzbar": OSError("Could not find module 'libzbar-64.dll'")},
    )
    assert mod.BACKEND_NAME in mod.KNOWN_BACKENDS
    assert mod.decode_barcodes(_qr_frame("SN-OK")) == ["SN-OK"]


def test_backend_description_mentions_backend():
    assert barcode_scanner.BACKEND_NAME.split("-")[0] in barcode_scanner.backend_description().lower()
