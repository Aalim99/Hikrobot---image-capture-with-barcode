from capture_engine import FLASH_SECONDS, CaptureEngine


def make(delay=2.0, lost=1.5):
    return CaptureEngine(capture_delay=delay, lost_reset=lost)


def see(engine, now, sn, raw=True, unreadable=False):
    engine.observe(now, sn, raw_detected=raw, unreadable=unreadable)


def watch(engine, now, sn):
    """One scanner cycle with the barcode still in view, then a tick."""
    see(engine, now, sn)
    return engine.tick(now)


def test_idle_engine_is_watching():
    engine = make()
    assert engine.tick(0.0).status.kind == "watching"


def test_new_barcode_counts_down_then_captures_once():
    engine = make(delay=2.0)
    first = watch(engine, 0.0, "SN1")
    assert first.status.kind == "countdown" and first.status.sn == "SN1"
    assert first.capture_sn is None

    mid = watch(engine, 1.0, "SN1")
    assert mid.status.kind == "countdown"
    assert mid.status.remaining == 1.0 and mid.status.fraction == 0.5

    done = watch(engine, 2.0, "SN1")
    assert done.capture_sn == "SN1"
    engine.report_saved("SN1", 1, now=2.0)

    # the item is still there: must NOT capture again
    see(engine, 2.1, "SN1")
    engine.tick(2.1)
    after = engine.tick(2.0 + FLASH_SECONDS + 0.1)
    assert after.capture_sn is None and after.status.kind == "already"
    assert engine.tick(30.0).capture_sn is None


def test_same_barcode_recaptures_only_after_it_left_view():
    engine = make(delay=1.0, lost=1.5)
    see(engine, 0.0, "SN1")
    engine.tick(0.0)
    assert engine.tick(1.0).capture_sn == "SN1"

    # leaves view, but not long enough yet
    see(engine, 1.5, None, raw=False)
    see(engine, 2.0, "SN1")
    assert engine.tick(2.0).capture_sn is None

    # truly gone for >= lost_reset seconds
    see(engine, 2.1, None, raw=False)
    see(engine, 3.7, None, raw=False)
    see(engine, 3.8, "SN1")
    assert engine.tick(3.8).status.kind == "countdown"
    assert engine.tick(4.8).capture_sn == "SN1"


def test_different_barcode_mid_countdown_restarts_for_the_new_one():
    engine = make(delay=2.0)
    watch(engine, 0.0, "A")
    watch(engine, 1.0, "A")
    status = watch(engine, 1.5, "B").status
    assert status.sn == "B" and status.remaining == 2.0

    assert watch(engine, 3.0, "B").capture_sn is None
    assert watch(engine, 3.5, "B").capture_sn == "B"


def test_stop_cancels_a_countdown_and_nothing_is_captured_while_stopped():
    engine = make(delay=2.0)
    watch(engine, 0.0, "SN1")

    engine.stop()
    stopped = watch(engine, 5.0, "SN1")
    assert stopped.status.kind == "stopped" and stopped.capture_sn is None

    # a board still in view is captured fresh once started again
    engine.start()
    assert watch(engine, 6.0, "SN1").status.kind == "countdown"
    assert watch(engine, 7.0, "SN1").capture_sn is None
    assert watch(engine, 8.0, "SN1").capture_sn == "SN1"


def test_barcode_captured_before_stop_does_not_refire_on_start():
    engine = make(delay=1.0)
    see(engine, 0.0, "SN1")
    engine.tick(0.0)
    assert engine.tick(1.0).capture_sn == "SN1"

    engine.stop()
    engine.start()
    see(engine, 2.0, "SN1")
    assert engine.tick(2.0).capture_sn is None


def test_new_barcode_placed_while_stopped_captures_on_start():
    engine = make(delay=1.0)
    engine.stop()
    see(engine, 0.0, "NEW")
    assert engine.tick(0.0).capture_sn is None
    engine.start()
    assert engine.tick(0.1).status.kind == "countdown"
    assert engine.tick(1.2).capture_sn == "NEW"


def test_pause_blocks_capture_and_cancels_countdown():
    engine = make(delay=1.0)
    see(engine, 0.0, "SN1")
    engine.tick(0.0)

    engine.set_paused(True)
    assert engine.tick(5.0).status.kind == "paused"
    assert engine.tick(5.0).capture_sn is None

    engine.set_paused(False)
    see(engine, 6.0, "SN1")
    assert engine.tick(6.0).status.kind == "countdown"


def test_blocked_camera_prevents_and_cancels_capture():
    engine = make(delay=2.0)
    see(engine, 0.0, "SN1")

    blocked = engine.tick(0.0, blocked="camera offline")
    assert blocked.status.kind == "blocked" and blocked.status.detail == "camera offline"
    assert blocked.capture_sn is None

    engine.tick(0.5)  # camera back: countdown starts
    cancelled = engine.tick(1.0, blocked="camera offline")
    assert cancelled.status.kind == "blocked"
    assert engine.tick(10.0, blocked="camera offline").capture_sn is None


def test_manual_capture_marks_barcode_so_it_does_not_double_fire():
    engine = make(delay=1.0)
    engine.begin_capture("MANUAL1", now=0.0)
    see(engine, 0.1, "MANUAL1")
    assert engine.tick(0.1).capture_sn is None


def test_saved_and_failed_flash_then_clear():
    engine = make()
    engine.report_saved("SN1", 2, now=10.0)
    engine.last_captured_sn = "SN1"
    saved = engine.tick(10.5).status
    assert saved.kind == "saved" and "attempt 2" in saved.detail
    assert engine.tick(10.0 + FLASH_SECONDS + 0.1).status.kind == "watching"

    engine.report_failed("disk full", now=20.0)
    failed = engine.tick(20.5).status
    assert failed.kind == "failed" and failed.detail == "disk full"


def test_unreadable_barcode_hint_only_when_nothing_decoded():
    engine = make()
    see(engine, 0.0, None, raw=False, unreadable=True)
    assert engine.tick(0.0).status.kind == "unreadable"

    see(engine, 0.1, None, raw=False, unreadable=False)
    assert engine.tick(0.1).status.kind == "watching"


def test_unstable_read_does_not_start_a_countdown():
    engine = make()
    see(engine, 0.0, None, raw=True)  # code seen but not yet confirmed stable
    assert engine.tick(0.0).status.kind == "watching"


def test_saving_state_lasts_until_the_result_is_reported():
    engine = make(delay=1.0)
    see(engine, 0.0, "SN1")
    engine.tick(0.0)
    fired = engine.tick(1.0)
    assert fired.capture_sn == "SN1" and fired.status.kind == "saving"

    # slow disk: still saving, and never fires a second capture meanwhile
    see(engine, 1.5, "SN1")
    waiting = engine.tick(1.5)
    assert waiting.status.kind == "saving" and waiting.status.sn == "SN1" and waiting.capture_sn is None

    engine.report_saved("SN1", 1, now=2.0)
    assert engine.tick(2.1).status.kind == "saved"


def test_failed_save_leaves_the_saving_state():
    engine = make(delay=1.0)
    engine.begin_capture("SN1", now=0.0)
    assert engine.tick(0.1).status.kind == "saving"
    engine.report_failed("disk full", now=0.2)
    assert engine.tick(0.3).status.kind == "failed"


def test_paused_status_shows_no_stale_barcode():
    engine = make()
    see(engine, 0.0, "SN1")
    engine.set_paused(True)
    assert engine.tick(0.0).status.sn is None


def test_barcode_leaving_during_the_countdown_cancels_the_capture():
    engine = make(delay=3.0, lost=1.0)
    assert watch(engine, 0.0, "SN1").status.kind == "countdown"

    see(engine, 0.5, None, raw=False)                     # gone ...
    assert engine.tick(0.5).status.kind == "countdown"    # ... but only briefly so far
    see(engine, 1.2, None, raw=False)
    cancelled = engine.tick(1.2)
    assert cancelled.status.kind == "watching" and cancelled.capture_sn is None

    see(engine, 3.5, None, raw=False)                     # the original deadline passes: nothing fires
    assert engine.tick(3.5).capture_sn is None


def test_a_brief_flicker_does_not_cancel_the_countdown():
    engine = make(delay=2.0, lost=1.5)
    watch(engine, 0.0, "SN1")
    see(engine, 0.4, None, raw=False)     # a hand passes over the label for a moment
    engine.tick(0.4)
    watch(engine, 0.8, "SN1")
    assert watch(engine, 2.0, "SN1").capture_sn == "SN1"


def test_a_stale_scan_result_cannot_start_a_capture():
    engine = make(delay=1.0)
    see(engine, 0.0, "SN1")
    assert engine.tick(60.0).status.kind == "watching"    # scanner went quiet a minute ago


def test_reset_observation_forgets_the_last_reading():
    engine = make(delay=1.0)
    see(engine, 0.0, "SN1", unreadable=True)
    engine.reset_observation()
    assert engine.tick(0.0).status.kind == "watching"
    assert engine.stable_sn is None and not engine.unreadable
