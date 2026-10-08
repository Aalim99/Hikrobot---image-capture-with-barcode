"""The auto-capture state machine, free of any GUI code so it can be tested.

States:

  WATCHING   - looking for a barcode in the scan area. A newly-seen,
               not-already-captured barcode starts a COUNTDOWN.
  COUNTDOWN  - configurable delay before the shot, so the operator has time
               to finish positioning the item. A different barcode appearing
               mid-countdown restarts it for the new one.

After a capture the barcode is suppressed (won't retrigger) until it has
been out of view for `lost_reset` seconds. That is what lets the same barcode
be captured again later as a new attempt_N for a genuine re-scan, without
spamming attempts while the item just sits there already captured.

Feed it with observe() whenever a scan finishes and call tick() on a timer.
"""
from dataclasses import dataclass

WATCHING = "WATCHING"
COUNTDOWN = "COUNTDOWN"

FLASH_SECONDS = 2.5
STALE_OBSERVATION_SECONDS = 5.0   # a scan result older than this is no longer trusted


@dataclass(frozen=True)
class Status:
    """What the UI should show right now. `kind` is one of:

    stopped, paused, countdown, saving, saved, failed, blocked, already,
    unreadable, watching.
    """
    kind: str
    sn: str | None = None        # barcode to show in the big readout
    detail: str = ""
    remaining: float = 0.0       # seconds left in a countdown
    fraction: float = 0.0        # 0..1 progress through the countdown


@dataclass(frozen=True)
class Tick:
    status: Status
    capture_sn: str | None = None  # set on the one tick that should save an image


class CaptureEngine:
    def __init__(self, capture_delay: float = 1.5, lost_reset: float = 1.5):
        self.capture_delay = capture_delay
        self.lost_reset = lost_reset

        self.running = True    # operator armed the station (Start/Stop)
        self.paused = False    # a dialog / scan-area edit is in progress

        self.state = WATCHING
        self.current_sn = None
        self.countdown_end = 0.0
        self.last_captured_sn = None
        self.last_seen_time = None

        self.stable_sn = None      # barcode confirmed by enough consecutive reads
        self.unreadable = False    # a barcode is visible but cannot be decoded
        self._observed_at = None   # when the last scan result arrived

        self._saving = False       # a capture was requested and is not written yet
        self._flash_until = 0.0
        self._flash_ok = True
        self._flash_text = ""

    # ---------- inputs ----------

    def observe(self, now: float, stable_sn, raw_detected: bool, unreadable: bool = False):
        """Record the outcome of one scan of the scan area."""
        self.stable_sn = stable_sn
        self.unreadable = unreadable
        self._observed_at = now
        if raw_detected:
            self.last_seen_time = now
        elif (
            self.last_captured_sn is not None
            and self.last_seen_time is not None
            and now - self.last_seen_time >= self.lost_reset
        ):
            self.last_captured_sn = None

    def reset_observation(self):
        """Forget the last scan result. Call when the scan area changes or
        scanning was switched off, so an old reading cannot start a capture."""
        self.stable_sn = None
        self.unreadable = False
        self._observed_at = None

    def start(self):
        self.running = True

    def stop(self):
        """Disarm. Abandons a countdown in flight rather than firing after Stop."""
        self.running = False
        self._cancel_countdown()
        self._flash_until = 0.0

    def set_paused(self, paused: bool):
        self.paused = paused
        if paused:
            self._cancel_countdown()

    def begin_capture(self, sn: str, now: float):
        """Record that a capture of `sn` was requested (auto or manual)."""
        self.last_captured_sn = sn
        self.last_seen_time = now
        self._saving = True
        self._cancel_countdown()

    def report_saved(self, sn: str, attempt: int, now: float):
        self._saving = False
        self._flash_ok = True
        self._flash_text = f"{sn}  attempt {attempt}"
        self._flash_until = now + FLASH_SECONDS

    def report_failed(self, message: str, now: float):
        self._saving = False
        self._flash_ok = False
        self._flash_text = message
        self._flash_until = now + FLASH_SECONDS * 2

    # ---------- per-tick decision ----------

    def tick(self, now: float, blocked: str | None = None) -> Tick:
        """Advance the machine. `blocked` is a reason capturing is impossible
        right now (camera offline...), or None."""
        fresh = self._observed_at is not None and now - self._observed_at <= STALE_OBSERVATION_SECONDS
        stable = self.stable_sn if fresh else None
        unreadable = self.unreadable and fresh

        if not self.running:
            return Tick(Status("stopped", sn=stable))

        if self.paused:
            self._cancel_countdown()
            return Tick(Status("paused"))

        if self.state == COUNTDOWN:
            if blocked:
                self._cancel_countdown()
                return Tick(Status("blocked", detail=blocked))
            if self.last_seen_time is None or now - self.last_seen_time >= self.lost_reset:
                # the barcode left before the shot: don't photograph an empty fixture
                self._cancel_countdown()
                return Tick(Status("watching"))

            if stable and stable != self.current_sn and stable != self.last_captured_sn:
                self._start_countdown(stable, now)

            remaining = self.countdown_end - now
            if remaining <= 0:
                sn = self.current_sn
                self.begin_capture(sn, now)
                return Tick(Status("saving", sn=sn), capture_sn=sn)

            return Tick(Status(
                "countdown",
                sn=self.current_sn,
                remaining=remaining,
                fraction=1 - remaining / max(self.capture_delay, 0.001),
            ))

        # WATCHING
        if stable and not blocked and stable != self.last_captured_sn:
            self._start_countdown(stable, now)
            return Tick(Status("countdown", sn=stable, remaining=self.capture_delay, fraction=0.0))

        if self._saving:
            return Tick(Status("saving", sn=self.last_captured_sn))
        if now < self._flash_until:
            kind = "saved" if self._flash_ok else "failed"
            return Tick(Status(kind, sn=self.last_captured_sn if self._flash_ok else None,
                               detail=self._flash_text))
        if blocked:
            return Tick(Status("blocked", detail=blocked))
        if stable:
            return Tick(Status("already", sn=stable))
        if unreadable:
            return Tick(Status("unreadable"))
        return Tick(Status("watching"))

    # ---------- internals ----------

    def _start_countdown(self, sn: str, now: float):
        self.state = COUNTDOWN
        self.current_sn = sn
        self.countdown_end = now + self.capture_delay
        self._flash_until = 0.0

    def _cancel_countdown(self):
        self.state = WATCHING
        self.current_sn = None
