"""Tests for `tools/firmware/debounce.py`.

Settle-time filter — the firmware holds a press candidate for the
configured window before publishing the new stable state. The
behaviour is verified with the public ``update`` and
``age_one_tick`` methods, driven with deterministic samples.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SESSION = _HERE.parent
sys.path.insert(0, str(_SESSION / "tools"))

import pytest  # noqa: E402
from firmware.debounce import DEBOUNCE_SETTLE_S, Debouncer  # noqa: E402


def test_initial_state_is_released() -> None:
    d = Debouncer()
    assert d.last_stable is False
    assert d.candidate is False


def test_single_sample_does_not_publish_immediately() -> None:
    """A single sample equal to the candidate must NOT publish early.

    A one-tick input shorter than the settle window is exactly the
    contact-bounce case. The debouncer must hold the previous
    stable state.
    """
    d = Debouncer()
    assert d.update(True, dt_s=DEBOUNCE_SETTLE_S / 2) is False


def test_settled_sample_publishes_after_settle_window() -> None:
    """A consistent candidate held for ``settle_s`` becomes stable."""
    d = Debouncer()
    assert d.update(True, dt_s=DEBOUNCE_SETTLE_S / 2) is False
    assert d.update(True, dt_s=DEBOUNCE_SETTLE_S / 2) is True


def test_intermittent_sample_resets_settle_clock() -> None:
    """A bouncing contact (raw True → False → True) restarts the window."""
    d = Debouncer()
    d.update(True, dt_s=DEBOUNCE_SETTLE_S / 2)
    d.update(False, dt_s=DEBOUNCE_SETTLE_S / 2)
    d.update(True, dt_s=DEBOUNCE_SETTLE_S / 4)
    assert d.last_stable is False


def test_release_after_publish_returns_to_false() -> None:
    """A clean release after a published press flips back to False."""
    d = Debouncer()
    d.update(True, dt_s=DEBOUNCE_SETTLE_S)
    assert d.last_stable is True
    d.update(False, dt_s=DEBOUNCE_SETTLE_S)
    assert d.last_stable is False


def test_age_one_tick_publishes_without_new_sample() -> None:
    """``age_one_tick`` advances the settle clock without a new raw sample."""
    d = Debouncer()
    d.update(True, dt_s=DEBOUNCE_SETTLE_S / 2)
    assert d.last_stable is False
    d.age_one_tick(dt_s=DEBOUNCE_SETTLE_S / 2)
    assert d.last_stable is True


def test_negative_dt_s_raises() -> None:
    d = Debouncer()
    with pytest.raises(ValueError, match="non-negative"):
        d.update(False, dt_s=-0.001)


def test_settle_window_constant_is_20_ms() -> None:
    assert DEBOUNCE_SETTLE_S == 0.020
