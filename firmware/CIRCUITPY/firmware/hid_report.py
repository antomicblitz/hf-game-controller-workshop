"""USB HID report composition for the exact-six control contract."""

from collections import namedtuple

from .gpio_map import CONTROL_IDS

HID_AXIS_MIN = -127
HID_AXIS_NEUTRAL = 0
HID_AXIS_MAX = 127
ACTION_A_HID_BUTTON = 1
ACTION_B_HID_BUTTON = 2
BUTTON_MASK_BITS = 16


class HIDReport(namedtuple("HIDReport", ["x", "y", "buttons"])):  # pyright: ignore[reportUntypedNamedTuple]
    """One immutable gamepad report with X/Y axes and a button mask."""

    x: int  # pyright: ignore[reportIncompatibleVariableOverride]
    y: int  # pyright: ignore[reportIncompatibleVariableOverride]
    buttons: int  # pyright: ignore[reportIncompatibleVariableOverride]

    def __init__(self, x: int, y: int, buttons: int) -> None:
        if not HID_AXIS_MIN <= x <= HID_AXIS_MAX:
            raise ValueError(f"x out of HID range: {x!r}")
        if not HID_AXIS_MIN <= y <= HID_AXIS_MAX:
            raise ValueError(f"y out of HID range: {y!r}")
        if buttons < 0 or buttons >= (1 << BUTTON_MASK_BITS):
            raise ValueError(
                f"buttons mask out of range 0..{(1 << BUTTON_MASK_BITS) - 1}: {buttons!r}"
            )


def _bit(button_number: int) -> int:
    """Convert a one-based HID button number into its mask bit."""
    if not 1 <= button_number <= BUTTON_MASK_BITS:
        raise ValueError(f"HID button number out of range 1..{BUTTON_MASK_BITS}: {button_number!r}")
    return 1 << (button_number - 1)


def _axis_value(negative_pressed: bool, positive_pressed: bool) -> int:
    """Return one axis value, collapsing a simultaneous opposite pair."""
    if negative_pressed and positive_pressed:
        return HID_AXIS_NEUTRAL
    if negative_pressed:
        return HID_AXIS_MIN
    if positive_pressed:
        return HID_AXIS_MAX
    return HID_AXIS_NEUTRAL


def _validate_control_states(control_states: object) -> "dict[str, bool]":
    """Validate the exact-six semantic-ID and boolean-value input contract."""
    if not isinstance(control_states, dict):
        raise ValueError("control_states must be a dict keyed by the six semantic IDs")
    states: "dict[str, bool]" = control_states  # pyright: ignore[reportAssignmentType, reportUnknownVariableType]  # noqa: UP037
    expected = set(CONTROL_IDS)
    provided = set(states.keys())

    missing = expected - provided
    if missing:
        raise ValueError(f"control_states missing IDs {sorted(missing)!r}")
    extras = provided - expected
    if extras:
        raise ValueError(f"control_states has unexpected IDs {sorted(extras, key=repr)!r}")

    for control_id in CONTROL_IDS:
        if states[control_id] is not True and states[control_id] is not False:
            raise ValueError(f"control state {control_id!r} must be bool")
    return states


def compute_hid_report(control_states: object) -> HIDReport:
    """Compose a report from exactly six semantic control states.

    Up/down drive Y -127/+127; left/right drive X -127/+127.  Opposite
    directions collapse to neutral on their shared axis.  ``action_a`` and
    ``action_b`` are HID buttons 1 and 2 and never participate in either axis.
    """
    states = _validate_control_states(control_states)
    x = _axis_value(states["left"], states["right"])
    y = _axis_value(states["up"], states["down"])
    buttons = 0
    if states["action_a"]:
        buttons |= _bit(ACTION_A_HID_BUTTON)
    if states["action_b"]:
        buttons |= _bit(ACTION_B_HID_BUTTON)
    return HIDReport(x=x, y=y, buttons=buttons)


def decode_active_low_pin(value: bool) -> bool:
    """Decode a single pulled-up GPIO value for callers at the boundary."""
    from .gpio_map import is_active_low_pressed

    return is_active_low_pressed(value)


__all__ = [
    "ACTION_A_HID_BUTTON",
    "ACTION_B_HID_BUTTON",
    "BUTTON_MASK_BITS",
    "HID_AXIS_MAX",
    "HID_AXIS_MIN",
    "HID_AXIS_NEUTRAL",
    "HIDReport",
    "compute_hid_report",
    "decode_active_low_pin",
]
