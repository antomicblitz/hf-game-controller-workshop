"""Software debouncing — settle-time filter for the GPIO inputs.

Why a debouncer in the firmware
===============================

Mechanical pushbuttons bounce: a single press can produce a brief
train of make/break edges over 1–10 ms before settling. The USB
HID stack sees every transition, so without debouncing one press
becomes several HID reports. We hold a press candidate for a
minimum settle window before publishing the new state.

Pure state machine
==================

The debouncer is a tiny Mealy machine with no hardware imports.
It avoids ``dataclasses`` so it imports cleanly on CircuitPython
8.x without requiring the optional ``dataclasses`` stdlib module.
"""

# Settle window — chosen so a single button bounce (≤ 10 ms typical,
# ≤ 25 ms worst case for PBS-33B) cannot leak through, while still
# feeling responsive to a human finger (~ 80–150 ms typical press).
DEBOUNCE_SETTLE_S = 0.020  # 20 ms


class Debouncer:
    """Settle-time debouncer.

    State diagram::

        raw == candidate          → no change
        raw != candidate          → candidate = raw, candidate_s = dt
        raw == candidate (later)  → candidate_s += dt (extends settle)
        candidate_s >= settle_s
        AND candidate != last_stable → publish candidate

    ``update(raw, dt_s)`` returns the latest **stable** state. The
    internal candidate is only published once it has survived the
    settle window. Callers should poll at the fixed 10 ms production
    period and pass ``dt_s`` as that period.
    """

    settle_s: float
    last_stable: bool
    candidate: bool
    candidate_age_s: float

    def __init__(self, settle_s: float = DEBOUNCE_SETTLE_S) -> None:
        if settle_s <= 0:
            raise ValueError(f"settle_s must be positive, got {settle_s!r}")
        self.settle_s = settle_s
        self.last_stable = False
        self.candidate = False
        self.candidate_age_s = 0.0

    def reset(self, initial: bool = False) -> None:
        """Reset internal state; useful between self-test cases."""
        self.last_stable = initial
        self.candidate = initial
        self.candidate_age_s = 0.0

    def update(self, raw: bool, dt_s: float) -> bool:
        """Advance the debouncer by ``dt_s`` seconds with a new raw sample.

        Returns the latest **stable** state. Callers compare against
        the previous return value to detect the rising/falling edge
        they should publish.
        """
        if dt_s < 0:
            raise ValueError(f"dt_s must be non-negative, got {dt_s!r}")

        if raw == self.candidate:
            # Sample matches the current candidate — extend the
            # settle window.
            self.candidate_age_s = self.candidate_age_s + dt_s
        else:
            # raw disagrees with the candidate — start a fresh
            # settle window with the new sample.
            self.candidate = raw
            self.candidate_age_s = dt_s

        if self.candidate_age_s >= self.settle_s and self.candidate != self.last_stable:
            self.last_stable = self.candidate
            self.candidate_age_s = 0.0
        return self.last_stable

    def age_one_tick(self, dt_s: float) -> bool:
        """Advance the settle clock without changing the raw sample.

        Useful in the main loop where you may have already polled
        several inputs and want to give the candidate time to mature
        without rewriting it. Returns the latest stable state.
        """
        if self.candidate_age_s >= self.settle_s:
            return self.last_stable
        self.candidate_age_s = self.candidate_age_s + dt_s
        if self.candidate_age_s >= self.settle_s and self.candidate != self.last_stable:
            self.last_stable = self.candidate
            self.candidate_age_s = 0.0
        return self.last_stable


__all__ = ["DEBOUNCE_SETTLE_S", "Debouncer"]
