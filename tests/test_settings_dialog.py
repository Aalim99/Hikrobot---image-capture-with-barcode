import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

import config
import hik_camera
from ui.settings_dialog import SettingsDialog
from ui.theme import STYLESHEET


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLESHEET)
    return app


def settings(**overrides):
    return {**config.DEFAULT_SETTINGS, **overrides}


def test_lists_connected_cameras_and_preselects_the_saved_one(fake, qapp):
    fake.FAKE.devices = [("MV-CS200-10UC", "DA111"), ("MV-CS200-10UC", "DA222")]
    dialog = SettingsDialog(None, settings(camera_serial="DA222"))

    labels = [dialog.camera_combo.itemText(i) for i in range(dialog.camera_combo.count())]
    assert labels[0].startswith("Automatic") and "MV-CS200-10UC  (DA111)" in labels
    assert dialog.camera_combo.currentData() == "DA222"
    assert dialog.camera_note.isHidden()


def test_saved_camera_that_is_unplugged_stays_selected(fake, qapp):
    fake.FAKE.devices = [("MV-CS200-10UC", "DA111")]
    dialog = SettingsDialog(None, settings(camera_serial="GONE"))
    assert dialog.camera_combo.currentData() == "GONE"
    assert "not connected" in dialog.camera_combo.currentText()
    assert dialog.values()["camera_serial"] == "GONE"      # saving must not silently switch cameras


def test_no_camera_and_missing_sdk_are_explained_not_fatal(fake, qapp, monkeypatch):
    fake.FAKE.devices = []
    dialog = SettingsDialog(None, settings())
    assert not dialog.camera_note.isHidden() and "USB 3.0" in dialog.camera_note.text()

    monkeypatch.setattr(hik_camera, "_sdk", None)
    monkeypatch.setattr(hik_camera, "mvimport_candidates", lambda: iter([]))
    dialog._refresh_cameras()
    assert "MvImport" in dialog.camera_note.text()
    assert dialog.camera_combo.count() == 1                # just "Automatic"


def test_values_reflect_the_edited_fields(fake, qapp):
    dialog = SettingsDialog(None, settings())
    dialog.output_dir.setText("  D:/Boards  ")
    dialog.jpeg_quality.setValue(80)
    dialog.exposure_auto.setChecked(False)
    dialog.exposure_us.setValue(4500)
    dialog.gain_auto.setChecked(False)
    dialog.gain_db.setValue(6.5)
    dialog.fps.setValue(12)
    dialog.delay.setValue(2.5)
    dialog.lost_reset.setValue(3)
    dialog.stable_reads.setValue(4)

    assert dialog.values() == {
        "output_dir": "D:/Boards", "jpeg_quality": 80, "camera_serial": "",
        "exposure_auto": False, "exposure_us": 4500.0, "gain_auto": False, "gain_db": 6.5,
        "acquisition_fps": 12.0, "capture_delay_seconds": 2.5,
        "barcode_lost_reset_seconds": 3.0, "barcode_stable_reads": 4,
    }
    assert set(dialog.values()) <= set(config.DEFAULT_SETTINGS)   # nothing unknown leaks into settings.json


def test_manual_fields_are_disabled_while_auto_is_on(fake, qapp):
    dialog = SettingsDialog(None, settings(exposure_auto=True, gain_auto=False))
    assert not dialog.exposure_us.isEnabled() and dialog.gain_db.isEnabled()
    dialog.exposure_auto.setChecked(False)
    dialog.gain_auto.setChecked(True)
    assert dialog.exposure_us.isEnabled() and not dialog.gain_db.isEnabled()


def test_demo_mode_leaves_camera_settings_alone(qapp):
    dialog = SettingsDialog(None, settings(), demo=True)
    values = dialog.values()
    assert not dialog.camera_combo.isEnabled() and not dialog.exposure_us.isEnabled()
    assert "camera_serial" not in values and "exposure_us" not in values
    assert values["output_dir"] == config.DEFAULT_SETTINGS["output_dir"]


def test_empty_output_folder_is_not_accepted(fake, qapp):
    dialog = SettingsDialog(None, settings())
    dialog.output_dir.setText("   ")
    dialog._accept()
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_dialog_is_tall_enough_for_its_wrapped_notes(fake, qapp):
    """Wrapped notes need extra height; without it the controls above get squashed."""
    fake.FAKE.devices = []                                   # shows the "no camera" note too
    dialog = SettingsDialog(None, settings())
    dialog.show()
    QTest.qWait(150)
    assert dialog.height() >= dialog.layout().totalHeightForWidth(dialog.width())
    for spin in (dialog.exposure_us, dialog.gain_db, dialog.fps, dialog.delay):
        assert spin.height() >= spin.sizeHint().height() - 2, "a control was squashed"
    dialog.close()
