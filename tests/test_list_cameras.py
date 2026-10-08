import list_cameras


def run(capsys, *argv):
    code = list_cameras.main(list(argv))
    return code, capsys.readouterr().out


def test_reports_missing_sdk(monkeypatch, capsys):
    import hik_camera
    monkeypatch.setattr(hik_camera, "_sdk", None)
    monkeypatch.setattr(hik_camera, "mvimport_candidates", lambda: iter([]))

    code, out = run(capsys)
    assert code == 1 and "[FAIL]" in out and "MvImport" in out


def test_lists_cameras(fake, capsys):
    fake.FAKE.devices = [("MV-CS200-10UC", "DA111")]
    code, out = run(capsys)

    assert code == 0
    assert "1 found" in out and "MV-CS200-10UC  (DA111)" in out and "--grab" in out


def test_reports_no_cameras(fake, capsys):
    fake.FAKE.devices = []
    code, out = run(capsys)
    assert code == 1 and "USB 3.0" in out


def test_grab_saves_a_frame(fake, capsys, tmp_path):
    target = tmp_path / "shot.jpg"
    code, out = run(capsys, "--grab", "--out", str(target))

    assert code == 0, out
    assert target.read_bytes()[:2] == b"\xff\xd8"
    assert "first frame received" in out and "64x48" in out and "BayerGB8" in out
    assert "Barcodes in that frame" in out


def test_grab_reports_busy_camera(fake, capsys, tmp_path):
    fake.FAKE.open_ret = 0x80000203
    code, out = run(capsys, "--grab", "--out", str(tmp_path / "x.jpg"))
    assert code == 1 and "0x80000203" in out
    assert not (tmp_path / "x.jpg").exists()
