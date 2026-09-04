"""Tests for `tools/firmware/scheduler.py`.

The deadline scheduler drives the production HID poll loop. The
contract is:

- The start-to-start period between two consecutive callback
  invocations is bounded by ``period_s`` (no accumulated
  post-work sleep).
- The reported ``dt_s`` is the actual time since the previous
  tick (used by the debouncer's settle-window accumulator).
- A custom ``clock`` and ``sleep`` are injectable so tests run
  deterministically without touching real time.

Tests use a fake clock and a recorded-sleep recorder — no real
``time.sleep``, no real ``time.monotonic``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SESSION = _HERE.parent
sys.path.insert(0, str(_SESSION / "tools"))

import pytest  # noqa: E402
from firmware.scheduler import DeadlineScheduler  # noqa: E402

from tests._pytest_helpers import approx  # noqa: E402


class _FakeClock:
    """Step through a deterministic monotonic timeline."""

    def __init__(self, start: float = 0.0) -> None:
        self.now: float = start
        self.calls: int = 0

    def __call__(self) -> float:
        self.calls += 1
        return self.now


class _RecordingSleep:
    """Record sleep durations instead of actually sleeping."""

    def __init__(self, advance_clock: _FakeClock) -> None:
        self._clock = advance_clock
        self.records: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.records.append(seconds)
        self._clock.now += seconds


def _noop_callback(_dt: float) -> None:
    pass


def _make_scheduler(period_s: float = 0.010):
    """Return (clock, sleep_recorder, scheduler) ready to drive."""
    clock = _FakeClock(start=0.0)
    sleep = _RecordingSleep(clock)
    dt_log: list[float] = []
    scheduler = DeadlineScheduler(
        period_s=period_s,
        callback=dt_log.append,
        clock=clock,
        sleep=sleep,
    )
    return clock, sleep, scheduler, dt_log


# ---------------------------------------------------------------------------
# Construction validation.
# ---------------------------------------------------------------------------
def test_scheduler_rejects_non_positive_period() -> None:
    with pytest.raises(ValueError, match="positive"):
        DeadlineScheduler(period_s=0, callback=_noop_callback)
    with pytest.raises(ValueError, match="positive"):
        DeadlineScheduler(period_s=-1.0, callback=_noop_callback)


# ---------------------------------------------------------------------------
# Deterministic behaviour — the contract is observable without real time.
# ---------------------------------------------------------------------------
def test_scheduler_first_tick_records_dt_equal_to_period() -> None:
    """The first tick's dt is exactly the configured period."""
    _clock, _sleep, scheduler, dt_log = _make_scheduler(period_s=0.010)
    scheduler.tick()
    assert dt_log == [0.010]


def test_scheduler_sleeps_until_next_deadline_on_early_tick() -> None:
    """A tick that runs before its deadline sleeps the remainder."""
    clock, sleep, scheduler, dt_log = _make_scheduler(period_s=0.010)
    # First tick: deadline is start + period = 10 ms; clock is at 0;
    # scheduler must sleep 10 ms.
    scheduler.tick()
    assert sleep.records == [0.010]
    # Clock has advanced by the sleep.
    assert clock.now == approx(0.010)
    assert dt_log == [approx(0.010)]


def test_scheduler_skips_sleep_when_late_and_keeps_dt_accurate() -> None:
    """A late tick catches up by skipping the sleep; dt reflects reality."""
    clock, sleep, scheduler, dt_log = _make_scheduler(period_s=0.010)
    # First tick fires on time.
    scheduler.tick()
    # Simulate the second tick being late: jump the clock past
    # the next deadline.
    clock.now = 0.030  # 10 ms past the second deadline (0.020)
    scheduler.tick()
    # No sleep was needed because we were late.
    assert sleep.records == [0.010]
    # The reported dt is the actual elapsed time since the previous
    # tick: 0.030 - 0.010 = 0.020.
    assert dt_log == [approx(0.010), approx(0.020)]


def test_scheduler_period_starts_at_tick_boundary_not_at_callback_end() -> None:
    """The deadline advances by ``period_s`` regardless of how long
    the callback took — start-to-start period stays bounded.

    With 3 ms of callback work and a 10 ms period, the sleep
    budget is 10 ms for the first tick (initial deadline) and
    7 ms for each subsequent tick (deadline 10 ms ahead of the
    work end). The clock ends at ``5 × (10 ms sleep + 3 ms work)``
    = 65 ms minus the last callback's work (which finishes after
    the 5th deadline was checked): 50 ms deadline + 5 × 3 ms = 65
    ms if all work is included; the last tick's clock ends at
    53 ms (deadline 50 + work 3) because we do not re-tick after
    the 5th callback.
    """
    clock, sleep, scheduler, _dt = _make_scheduler(period_s=0.010)

    def slow_callback(_dt: float) -> None:
        clock.now += 0.003

    scheduler.callback = slow_callback

    for _ in range(5):
        scheduler.tick()

    # First tick sleeps the full 10 ms (clock starts at 0); the
    # remaining four ticks sleep 7 ms (deadline 10 ms ahead, work
    # already consumed 3 ms).
    assert sleep.records == [approx(0.010)] + [approx(0.007)] * 4
    # After the 5th tick the clock sits at deadline (50 ms) plus
    # the callback work (3 ms) = 53 ms.
    assert clock.now == approx(0.053)


def test_scheduler_no_accumulated_post_work_sleep() -> None:
    """If the callback takes 5 ms and the period is 10 ms, the next
    tick sleeps only 5 ms — not 10 ms after work plus 10 ms for
    period."""
    clock, sleep, scheduler, _dt = _make_scheduler(period_s=0.010)

    work_time_s = 0.005

    def slow_callback(_dt: float) -> None:
        clock.now += work_time_s

    scheduler.callback = slow_callback

    # First tick: deadline is 10 ms; callback takes 5 ms; second
    # tick must sleep 5 ms (10 - 5), not 10 ms.
    scheduler.tick()
    scheduler.tick()
    # First sleep: 10 ms (clock at 0). Second sleep: 5 ms (clock
    # at 0.010 = work_5ms + sleep_5ms).
    assert sleep.records == [approx(0.010), approx(0.005)]


def test_scheduler_tick_count_increments() -> None:
    _clock, _sleep, scheduler, _dt = _make_scheduler()
    assert scheduler.tick_count == 0
    scheduler.tick()
    assert scheduler.tick_count == 1
    scheduler.tick()
    scheduler.tick()
    assert scheduler.tick_count == 3


def test_scheduler_reset_re_anchors_deadlines() -> None:
    _clock, _sleep, scheduler, dt_log = _make_scheduler(period_s=0.010)
    scheduler.tick()
    assert dt_log == [approx(0.010)]
    scheduler.reset(now=1.000)
    assert scheduler.tick_count == 0
    scheduler.tick()
    # After reset at t=1.000, the first dt is the period (10 ms)
    # measured from the reset anchor.
    assert dt_log == [approx(0.010), approx(0.010)]


def test_scheduler_default_clock_is_time_monotonic() -> None:
    """The default ``clock`` is ``time.monotonic``; this is a smoke
    test that the dependency-injection default exists."""
    import time

    scheduler = DeadlineScheduler(period_s=0.010, callback=_noop_callback)
    assert scheduler._clock is time.monotonic  # pyright: ignore[reportPrivateUsage]
    assert scheduler._sleep is time.sleep  # pyright: ignore[reportPrivateUsage]


def test_scheduler_passes_measured_dt_to_callback() -> None:
    """The dt reported to the callback is the wall-clock delta from
    the previous tick — the debouncer uses it to settle."""
    clock, _sleep, scheduler, dt_log = _make_scheduler(period_s=0.010)
    # First tick: dt = period (clock at 0).
    scheduler.tick()
    # Simulate a long delay before the second tick.
    clock.now = 0.025
    scheduler.tick()
    assert dt_log[-1] == approx(0.015)  # 25 - 10 ms since previous tick
