import pytest
from PySide6.QtCore import QEvent
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


@pytest.fixture
def make_dialog(qapp):
    """Build dialogs and tear them down before the QApplication goes away.

    Leaving top-level widgets alive until interpreter shutdown crashes PySide6
    at exit (it destroys the QApplication first).
    """
    made = []

    def make(*args, **kwargs):
        dialog = SettingsDialog(*args, **kwargs)
        made.append(dialog)
        return dialog

    yield make
    for dialog in made:
        dialog.close()
        dialog.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def settings(**overrides):
    return {**config.DEFAULT_SETTINGS, **overrides}


def test_lists_connected_cameras_and_preselects_the_saved_one(fake, make_dialog):
    fake.FAKE.devices = [("MV-CS200-10UC", "DA111"), ("MV-CS200-10UC", "DA222")]
    dialog = make_dialog(None, settings(camera_serial="DA222"))

    labels = [dialog.camera_combo.itemText(i) for i in range(dialog.camera_combo.count())]
    assert labels[0].startswith("Automatic") and "MV-CS200-10UC  (DA111)" in labels
    assert dialog.camera_combo.currentData() == "DA222"
    assert dialog.camera_note.isHidden()


def test_saved_camera_that_is_unplugged_stays_selected(fake, make_dialog):
    fake.FAKE.devices = [("MV-CS200-10UC", "DA111")]
    dialog = make_dialog(None, settings(camera_serial="GONE"))
    assert dialog.camera_combo.currentData() == "GONE"
    assert "not connected" in dialog.camera_combo.currentText()
    assert dialog.values()["camera_serial"] == "GONE"      # saving must not silently switch cameras


def test_no_camera_and_missing_sdk_are_explained_not_fatal(fake, make_dialog, monkeypatch):
    fake.FAKE.devices = []
    dialog = make_dialog(None, settings())
    assert not dialog.camera_note.isHidden() and "USB 3.0" in dialog.camera_note.text()

    monkeypatch.setattr(hik_camera, "_sdk", None)
    monkeypatch.setattr(hik_camera, "mvimport_candidates", lambda: iter([]))
    dialog._refresh_cameras()
    assert "MvImport" in dialog.camera_note.text()
    assert dialog.camera_combo.count() == 1                # just "Automatic"


def test_values_reflect_the_edited_fields(fake, make_dialog):
    dialog = make_dialog(None, settings())
    dialog.output_dir.setText("  D:/Boards  ")
    dialog.image_format.setCurrentIndex(dialog.image_format.findData("png"))
    dialog.jpeg_quality.setValue(80)
    dialog.exposure_auto.setChecked(False)
    dialog.exposure_us.setValue(4500)
    dialog.gain_auto.setChecked(False)
    dialog.gain_db.setValue(6.5)
    dialog.wb_combo.setCurrentIndex(dialog.wb_combo.findData("auto"))
    dialog.gamma_override.setChecked(True)
    dialog.gamma.setValue(2.2)
    dialog.fps.setValue(12)
    dialog.delay.setValue(2.5)
    dialog.lost_reset.setValue(3)
    dialog.stable_reads.setValue(4)

    assert dialog.values() == {
        "output_dir": "D:/Boards", "image_format": "png", "jpeg_quality": 80, "camera_serial": "",
        "exposure_auto": False, "exposure_us": 4500.0, "gain_auto": False, "gain_db": 6.5,
        "white_balance": "auto", "gamma_override": True, "gamma": 2.2,
        "acquisition_fps": 12.0, "capture_delay_seconds": 2.5,
        "barcode_lost_reset_seconds": 3.0, "barcode_stable_reads": 4,
    }
    assert set(dialog.values()) <= set(config.DEFAULT_SETTINGS)   # nothing unknown leaks into settings.json


def test_saved_values_show_up_in_the_fields(fake, make_dialog):
    dialog = make_dialog(None, settings(image_format="png", white_balance="off", gamma_override=True, gamma=2.5))
    assert dialog.image_format.currentData() == "png"
    assert dialog.wb_combo.currentData() == "off"
    assert dialog.gamma_override.isChecked() and dialog.gamma.value() == 2.5


def test_defaults_leave_the_camera_alone(fake, make_dialog):
    values = make_dialog(None, settings()).values()
    assert values["white_balance"] == "camera" and values["gamma_override"] is False
    assert values["image_format"] == "jpg"


def test_jpeg_quality_only_matters_for_jpeg(fake, make_dialog):
    dialog = make_dialog(None, settings())
    assert dialog.jpeg_quality.isEnabled()
    dialog.image_format.setCurrentIndex(dialog.image_format.findData("png"))
    assert not dialog.jpeg_quality.isEnabled()
    dialog.image_format.setCurrentIndex(dialog.image_format.findData("jpg"))
    assert dialog.jpeg_quality.isEnabled()


def test_gamma_value_is_only_editable_when_set_is_ticked(fake, make_dialog):
    dialog = make_dialog(None, settings())
    assert not dialog.gamma.isEnabled()
    dialog.gamma_override.setChecked(True)
    assert dialog.gamma.isEnabled()


def test_balance_now_calls_the_camera_and_reports_the_outcome(fake, make_dialog):
    calls = []
    dialog = make_dialog(None, settings(), on_balance_once=lambda: calls.append(1) or True)
    assert dialog.balance_btn.isEnabled() and dialog.balance_note.isHidden()
    dialog.balance_btn.click()
    assert calls == [1]
    assert not dialog.balance_note.isHidden() and "measured" in dialog.balance_note.text()

    refused = make_dialog(None, settings(), on_balance_once=lambda: False)
    refused.balance_btn.click()
    assert "did not accept" in refused.balance_note.text()


def test_balance_now_is_unavailable_without_a_camera_to_ask(fake, make_dialog):
    assert not make_dialog(None, settings()).balance_btn.isEnabled()                       # no callback
    assert not make_dialog(None, settings(), demo=True, on_balance_once=lambda: True).balance_btn.isEnabled()


def test_manual_fields_are_disabled_while_auto_is_on(fake, make_dialog):
    dialog = make_dialog(None, settings(exposure_auto=True, gain_auto=False))
    assert not dialog.exposure_us.isEnabled() and dialog.gain_db.isEnabled()
    dialog.exposure_auto.setChecked(False)
    dialog.gain_auto.setChecked(True)
    assert dialog.exposure_us.isEnabled() and not dialog.gain_db.isEnabled()


def test_demo_mode_leaves_camera_settings_alone(make_dialog):
    dialog = make_dialog(None, settings(), demo=True)
    values = dialog.values()
    assert not dialog.camera_combo.isEnabled() and not dialog.exposure_us.isEnabled()
    assert "camera_serial" not in values and "exposure_us" not in values
    assert values["output_dir"] == config.DEFAULT_SETTINGS["output_dir"]
    assert "white_balance" not in values and "gamma" not in values
    assert not dialog.wb_combo.isEnabled() and not dialog.gamma_override.isEnabled()
    assert values["image_format"] == "jpg"       # saving options still apply in demo mode


def test_empty_output_folder_is_not_accepted(fake, make_dialog):
    dialog = make_dialog(None, settings())
    dialog.output_dir.setText("   ")
    dialog._accept()
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_dialog_is_tall_enough_for_its_wrapped_notes(fake, make_dialog):
    """Wrapped notes need extra height; without it the controls above get squashed."""
    fake.FAKE.devices = []                                   # shows the "no camera" note too
    dialog = make_dialog(None, settings())
    dialog.show()
    QTest.qWait(150)
    assert dialog.height() >= dialog.layout().totalHeightForWidth(dialog.width())
    for spin in (dialog.exposure_us, dialog.gain_db, dialog.fps, dialog.delay):
        assert spin.height() >= spin.sizeHint().height() - 2, "a control was squashed"
    dialog.close()
