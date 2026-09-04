"""Behavioral tests for the active six-pin polling path."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SESSION = _HERE.parent
sys.path.insert(0, str(_SESSION / "tools"))

import pytest  # noqa: E402
from firmware.gpio_map import CONTROL_IDS  # noqa: E402
from firmware.polling import (  # noqa: E402
    POLL_HZ,
    POLL_PERIOD_S,
    all_released,
    decode_control_states,
    read_control_states,
)


class Pin:
    def __init__(self, value: bool) -> None:
        self.value = value


def test_polling_target_is_100_hz() -> None:
    assert POLL_HZ == 100
    assert POLL_PERIOD_S == 0.010


def test_decode_control_states_uses_frozen_semantic_ids() -> None:
    states: dict[str, bool] = decode_control_states((False, True, True, True, False, False))
    assert tuple(states) == CONTROL_IDS
    assert states["up"] is True
    assert states["action_a"] is True
    assert states["action_b"] is True


def test_read_control_states_reads_six_pins_and_decodes_active_low() -> None:
    assert read_control_states(
        [Pin(True), Pin(False), Pin(True), Pin(True), Pin(True), Pin(False)]
    ) == {
        "up": False,
        "down": True,
        "right": False,
        "left": False,
        "action_a": False,
        "action_b": True,
    }


def test_all_released_returns_exact_six_idle_state() -> None:
    assert all_released() == {control_id: False for control_id in CONTROL_IDS}


@pytest.mark.parametrize("pin_count", [0, 5, 7])
def test_read_control_states_rejects_non_six_pin_sets(pin_count: int) -> None:
    with pytest.raises(ValueError, match="exactly 6"):
        read_control_states([Pin(True)] * pin_count)
