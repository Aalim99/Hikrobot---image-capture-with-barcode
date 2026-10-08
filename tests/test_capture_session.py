import numpy as np

from capture_session import next_attempt_number, sanitize_sn, save_capture


def _image(color=0):
    return np.full((60, 80, 3), color, dtype=np.uint8)


def test_sanitize_sn_removes_illegal_chars():
    assert sanitize_sn('AB<>:"/\\|?*CD') == "AB_________CD"


def test_sanitize_sn_empty_falls_back():
    assert sanitize_sn("   ") == "UNKNOWN_SN"
    assert sanitize_sn("...") == "UNKNOWN_SN"


def test_sanitize_sn_strips_whitespace():
    assert sanitize_sn("  SN123  ") == "SN123"


def test_sanitize_sn_avoids_windows_reserved_names():
    assert sanitize_sn("CON") != "CON"
    assert sanitize_sn("com1") != "com1"
    assert sanitize_sn("NUL.txt") != "NUL.txt"
    assert sanitize_sn("CONSOLE") == "CONSOLE"  # only the exact reserved names


def test_save_capture_creates_barcode_folder_with_image_and_log(tmp_path):
    saved = save_capture(
        output_dir=str(tmp_path),
        sn="SN-001",
        image_bgr=_image(10),
        details={"Camera": "MV-CS200-10UC (DA1234567)", "Exposure": "10000 us"},
        jpeg_quality=90,
    )

    assert saved.folder == tmp_path / "SN-001"
    assert saved.image_path == tmp_path / "SN-001" / "SN-001_attempt_1.jpg"
    assert saved.attempt == 1
    assert saved.image_path.read_bytes()[:2] == b"\xff\xd8"  # a real JPEG

    log = (saved.folder / "log.txt").read_text(encoding="utf-8")
    assert "Attempt 1" in log
    assert "Barcode: SN-001" in log
    assert "SN-001_attempt_1.jpg (80x60" in log
    assert "Camera: MV-CS200-10UC (DA1234567)" in log


def test_save_capture_versions_repeat_scans_without_overwriting(tmp_path):
    kwargs = dict(output_dir=str(tmp_path), sn="SN-DUP", image_bgr=_image(1))
    first = save_capture(**kwargs)
    first_bytes = first.image_path.read_bytes()
    second = save_capture(**{**kwargs, "image_bgr": _image(200)})

    assert (first.attempt, second.attempt) == (1, 2)
    assert first.image_path != second.image_path
    assert first.image_path.read_bytes() == first_bytes
    assert second.image_path.exists()

    log = (tmp_path / "SN-DUP" / "log.txt").read_text(encoding="utf-8")
    assert "Attempt 1" in log and "Attempt 2" in log  # appended, not replaced


def test_sn_with_illegal_chars_still_produces_valid_folder(tmp_path):
    saved = save_capture(output_dir=str(tmp_path), sn="SN/123:BAD", image_bgr=_image())
    assert saved.folder.name == "SN_123_BAD"
    assert saved.image_path.name == "SN_123_BAD_attempt_1.jpg"
    assert "Barcode: SN/123:BAD" in (saved.folder / "log.txt").read_text(encoding="utf-8")


def test_unicode_output_path_works(tmp_path):
    # cv2.imwrite silently writes nothing for non-ASCII paths on Windows
    saved = save_capture(output_dir=str(tmp_path / "gambar_é_日本"), sn="SN-1", image_bgr=_image())
    assert saved.image_path.exists() and saved.image_path.stat().st_size > 0


def test_no_partial_files_left_behind(tmp_path):
    saved = save_capture(output_dir=str(tmp_path), sn="SN-CLEAN", image_bgr=_image())
    assert sorted(p.name for p in saved.folder.iterdir()) == ["SN-CLEAN_attempt_1.jpg", "log.txt"]


def test_next_attempt_number_ignores_unrelated_files(tmp_path):
    folder = tmp_path / "SN9"
    folder.mkdir()
    (folder / "SN9_attempt_1.jpg").write_bytes(b"x")
    (folder / "SN9_attempt_4.jpg").write_bytes(b"x")
    (folder / "SN9_attempt_x.jpg").write_bytes(b"x")
    (folder / "other_attempt_9.jpg").write_bytes(b"x")
    (folder / "log.txt").write_text("")

    assert next_attempt_number(folder, "SN9") == 5
    assert next_attempt_number(tmp_path / "missing", "SN9") == 1


def test_sn_with_regex_characters_numbers_correctly(tmp_path):
    kwargs = dict(output_dir=str(tmp_path), sn="A+B(1)[x]", image_bgr=_image())
    assert save_capture(**kwargs).attempt == 1
    assert save_capture(**kwargs).attempt == 2
