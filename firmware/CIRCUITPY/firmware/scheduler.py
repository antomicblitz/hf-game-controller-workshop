"""Deadline scheduler for the production HID poll loop.

Why a deadline scheduler?
=========================

A naive ``while True: ... ; time.sleep(period)`` loop has a
start-to-start period of ``work_time + sleep_time``. If the work
takes 5 ms and the sleep is 10 ms, the loop runs at 67 Hz, not
100 Hz. Over time the period can drift even further if work
takes longer than expected.

A **deadline scheduler** instead targets the next absolute
deadline ``last_tick + period``. If work finishes early, the
scheduler sleeps only the remaining slice; if work finishes late,
it skips the sleep and runs immediately. The reported
``dt_s`` is the actual time since the previous tick, which the
debouncer uses to settle mechanical-bounce filtering.

This module is host-testable: it accepts a ``clock`` and a
``sleep`` call so tests inject a deterministic fake. It avoids
``from __future__ import annotations``, ``dataclasses``, and
``collections.abc`` so it imports cleanly on CircuitPython 8.x
without the optional CPython stdlib modules — but the scheduler
is currently used only by the production poll loop on the
device, never by host tests of the deploy boundary.
"""

import time


class DeadlineScheduler:
    """Run a callback at a fixed start-to-start period via absolute deadlines.

    Each call to :meth:`tick` waits until the next absolute
    deadline (``_next_deadline``), invokes the callback with the
    measured ``dt_s`` since the previous tick, and then advances
    the deadline by one period. The advance happens **before**
    the next sleep, so the period between consecutive
    ``callback`` invocations is bounded by ``period_s`` even when
    the callback runs long.

    Parameters
    ----------
    period_s:
        Target start-to-start period in seconds (e.g. ``0.010``).
    callback:
        Callable invoked once per tick with the measured ``dt_s``.
        Must not raise; a raise aborts the loop.
    clock:
        Callable returning the current monotonic time in seconds
        (default: ``time.monotonic``). Injectable for tests.
    sleep:
        Callable accepting a duration in seconds (default:
        ``time.sleep``). Injectable for tests.
    """

    callback: object
    _clock: object
    _sleep: object
    _last_tick: float
    _next_deadline: float

    def __init__(
        self, period_s: float, callback: object, clock: object = None, sleep: object = None
    ) -> None:
        if period_s <= 0:
            raise ValueError(f"period_s must be positive, got {period_s!r}")
        self.period_s = period_s
        self.callback = callback
        self._clock: object = clock if clock is not None else time.monotonic
        self._sleep: object = sleep if sleep is not None else time.sleep
        self._last_tick = self._clock()  # pyright: ignore[reportCallIssue, reportUnknownMemberType]
        self._next_deadline = self._last_tick + period_s  # pyright: ignore[reportUnknownMemberType]
        self._tick_count = 0

    @property
    def tick_count(self) -> int:
        """Number of completed :meth:`tick` calls (for diagnostics)."""
        return self._tick_count

    def reset(self, now: object = None) -> None:
        """Reset the deadline clock; useful between self-test cases.

        Args:
            now: monotonic time to anchor the next deadline. Defaults
                to the configured ``clock()`` value.
        """
        anchor: float = now if now is not None else self._clock()  # pyright: ignore[reportAssignmentType, reportCallIssue, reportUnknownMemberType, reportUnknownVariableType]
        self._last_tick = anchor
        self._next_deadline = anchor + self.period_s
        self._tick_count = 0

    def tick(self) -> float:
        """Run one period; invoke the callback with the measured ``dt_s``.

        Returns:
            The measured ``dt_s`` (actual elapsed time since the
            previous tick). Callers normally ignore the return
            value; it is exposed for tests and for the production
            loop's optional logging.
        """
        now: float = self._clock()  # pyright: ignore[reportCallIssue, reportUnknownMemberType, reportUnknownVariableType]
        # If we are early, sleep until the next deadline; if late,
        # skip the sleep so we catch up as fast as possible.
        sleep_for: float = self._next_deadline - now  # pyright: ignore[reportUnknownVariableType]
        if sleep_for > 0:
            self._sleep(sleep_for)  # pyright: ignore[reportCallIssue, reportUnknownMemberType]
            now = self._clock()  # pyright: ignore[reportCallIssue, reportUnknownMemberType, reportUnknownVariableType]
        dt_s: float = now - self._last_tick  # pyright: ignore[reportUnknownVariableType]
        # Advance the deadline by exactly one period regardless of
        # catch-up state; this is the "no accumulated 10 ms after
        # work" property.
        self._last_tick = now
        self._next_deadline = self._next_deadline + self.period_s
        self._tick_count = self._tick_count + 1
        self.callback(dt_s)  # pyright: ignore[reportCallIssue, reportUnknownMemberType]
        return dt_s  # pyright: ignore[reportUnknownVariableType]


__all__ = ["DeadlineScheduler"]
