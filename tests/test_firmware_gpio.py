"""Behavioral tests for the exact-six Feather GPIO harness."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SESSION = _HERE.parent
sys.path.insert(0, str(_SESSION / "tools"))

import pytest  # noqa: E402
from firmware.gpio_map import (  # noqa: E402
    CONTROL_COUNT,
    CONTROL_IDS,
    CONTROL_ROLES,
    GPIO_NAMES_BY_CONTROL,
    control_states_from_raw,
    control_to_gpio_name,
    gpio_index_by_name,
    is_active_low_pressed,
)


def test_exact_six_control_order_and_gpio_map() -> None:
    assert CONTROL_IDS == ("up", "down", "right", "left", "action_a", "action_b")
    assert CONTROL_ROLES == ("UP", "DOWN", "RIGHT", "LEFT", "ACTION_A", "ACTION_B")
    assert GPIO_NAMES_BY_CONTROL == ("D5", "D6", "D9", "D10", "D11", "D12")
    assert CONTROL_COUNT == 6


@pytest.mark.parametrize(
    "control_id,pin_name",
    list(zip(CONTROL_IDS, GPIO_NAMES_BY_CONTROL, strict=True)),
)
def test_control_to_gpio_name_matches_frozen_order(control_id: str, pin_name: str) -> None:
    assert control_to_gpio_name(control_id) == pin_name


def test_gpio_index_round_trips_frozen_names() -> None:
    for index, pin_name in enumerate(GPIO_NAMES_BY_CONTROL):
        assert gpio_index_by_name(pin_name) == index


def test_unknown_control_or_gpio_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown control ID"):
        control_to_gpio_name("center")
    with pytest.raises(ValueError, match="unknown GPIO name"):
        gpio_index_by_name("A0")


def test_active_low_decoder_maps_high_to_released_and_low_to_pressed() -> None:
    assert is_active_low_pressed(True) is False
    assert is_active_low_pressed(False) is True


def test_raw_gpio_sample_decodes_to_semantic_ids() -> None:
    states: dict[str, bool] = control_states_from_raw((False, True, True, True, False, False))
    assert states == {
        "up": True,
        "down": False,
        "right": False,
        "left": False,
        "action_a": True,
        "action_b": True,
    }


def test_raw_gpio_sample_rejects_wrong_length_and_non_boolean_values() -> None:
    with pytest.raises(ValueError, match="exactly 6"):
        control_states_from_raw((True,) * 5)
    with pytest.raises(ValueError, match="must be bool"):
        control_states_from_raw((True, True, True, True, True, 0))
