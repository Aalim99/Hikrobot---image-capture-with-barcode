"""settings.json is per-machine runtime state. It must be created when
missing, but never rewritten when nothing changed - an unconditional
rewrite on every launch dirties the working tree and blocks git pull.
"""
import json

import config


def _use_temp_settings(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_PATH", path)
    return path


def test_creates_file_with_defaults_when_missing(tmp_path, monkeypatch):
    path = _use_temp_settings(tmp_path, monkeypatch)
    settings = config.load_settings()

    assert path.exists()
    assert settings == config.DEFAULT_SETTINGS
    assert json.loads(path.read_text()) == config.DEFAULT_SETTINGS


def test_does_not_rewrite_an_up_to_date_file(tmp_path, monkeypatch):
    _use_temp_settings(tmp_path, monkeypatch)
    config.load_settings()

    writes = []
    monkeypatch.setattr(config, "save_settings", lambda s: writes.append(s))
    config.load_settings()

    assert writes == [], "loading unchanged settings must not write the file"


def test_adds_newly_introduced_keys(tmp_path, monkeypatch):
    path = _use_temp_settings(tmp_path, monkeypatch)
    path.write_text(json.dumps({"output_dir": "D:/Boards"}))

    settings = config.load_settings()

    assert settings["output_dir"] == "D:/Boards"  # user value kept
    assert settings["capture_delay_seconds"] == config.DEFAULT_SETTINGS["capture_delay_seconds"]
    assert json.loads(path.read_text())["output_dir"] == "D:/Boards"


def test_corrupt_file_falls_back_to_defaults(tmp_path, monkeypatch):
    path = _use_temp_settings(tmp_path, monkeypatch)
    path.write_text("{not valid json")

    assert config.load_settings() == config.DEFAULT_SETTINGS


def test_non_object_json_falls_back_to_defaults(tmp_path, monkeypatch):
    path = _use_temp_settings(tmp_path, monkeypatch)
    path.write_text("[1, 2, 3]")

    assert config.load_settings() == config.DEFAULT_SETTINGS


def test_scan_area_roundtrips_through_the_file(tmp_path, monkeypatch):
    _use_temp_settings(tmp_path, monkeypatch)
    settings = config.load_settings()
    assert settings["roi"] is None

    settings["roi"] = [0.25, 0.3, 0.4, 0.2]
    config.save_settings(settings)
    assert config.load_settings()["roi"] == [0.25, 0.3, 0.4, 0.2]
