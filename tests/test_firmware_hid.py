"""Behavioral tests for exact-six semantic HID composition."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SESSION = _HERE.parent
sys.path.insert(0, str(_SESSION / "tools"))

import pytest  # noqa: E402
from firmware.gpio_map import CONTROL_IDS  # noqa: E402
from firmware.hid_report import (  # noqa: E402
    ACTION_A_HID_BUTTON,
    ACTION_B_HID_BUTTON,
    HID_AXIS_MAX,
    HID_AXIS_MIN,
    HID_AXIS_NEUTRAL,
    HIDReport,
    compute_hid_report,
)


def _states(*pressed: str) -> dict[str, bool]:
    return {control_id: control_id in pressed for control_id in CONTROL_IDS}


@pytest.mark.parametrize(
    "pressed,expected",
    [
        ("up", (0, -127, 0)),
        ("down", (0, 127, 0)),
        ("right", (127, 0, 0)),
        ("left", (-127, 0, 0)),
        ("action_a", (0, 0, 1)),
        ("action_b", (0, 0, 2)),
    ],
)
def test_each_semantic_control_maps_to_frozen_hid_output(
    pressed: str, expected: tuple[int, int, int]
) -> None:
    assert compute_hid_report(_states(pressed)) == expected


def test_actions_use_independent_hid_buttons_and_compose() -> None:
    assert compute_hid_report(_states("action_a")).buttons == 1 << (ACTION_A_HID_BUTTON - 1)
    assert compute_hid_report(_states("action_b")).buttons == 1 << (ACTION_B_HID_BUTTON - 1)
    assert compute_hid_report(_states("action_a", "action_b")).buttons == 3


def test_opposite_direction_pairs_collapse_to_neutral() -> None:
    assert compute_hid_report(_states("up", "down", "left", "right")) == (
        HID_AXIS_NEUTRAL,
        HID_AXIS_NEUTRAL,
        0,
    )


def test_perpendicular_directions_remain_independent() -> None:
    assert compute_hid_report(_states("up", "right")) == (HID_AXIS_MAX, HID_AXIS_MIN, 0)


def test_actions_do_not_participate_in_direction_axes() -> None:
    assert compute_hid_report(_states("up", "action_a", "action_b")) == (0, -127, 3)


@pytest.mark.parametrize(
    "states,expected_message",
    [
        ({"up": False, "down": False, "right": False, "left": False}, "missing IDs"),
        ({**_states(), "center": False}, "unexpected IDs"),
        ({**_states(), "action_a": 1}, "must be bool"),
    ],
)
def test_malformed_semantic_states_fail_closed(states: object, expected_message: str) -> None:
    with pytest.raises(ValueError, match=expected_message):
        compute_hid_report(states)


def test_hid_report_rejects_invalid_axis_and_mask_values() -> None:
    with pytest.raises(ValueError, match="x out of HID range"):
        HIDReport(x=-128, y=0, buttons=0)
    with pytest.raises(ValueError, match="y out of HID range"):
        HIDReport(x=0, y=128, buttons=0)
    with pytest.raises(ValueError, match="buttons mask out of range"):
        HIDReport(x=0, y=0, buttons=1 << 16)
