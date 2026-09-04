"""Exact-six Feather GPIO map for the active controller.

The active harness has four normally-open PBS-33B cardinal buttons and two
normally-open GUUZI action buttons.  The order in this module is frozen by the
active controller contract and is shared by the runtime and host self-test.

Every input uses the Feather's internal pull-up.  A released input therefore
reads HIGH and a pressed input, shorted to common GND, reads LOW.
"""

CONTROL_IDS = ("up", "down", "right", "left", "action_a", "action_b")
CONTROL_ROLES = ("UP", "DOWN", "RIGHT", "LEFT", "ACTION_A", "ACTION_B")
GPIO_NAMES_BY_CONTROL = ("D5", "D6", "D9", "D10", "D11", "D12")
CONTROL_COUNT = len(CONTROL_IDS)


def control_to_gpio_name(control_id: str) -> str:
    """Return the frozen Feather GPIO name for one semantic control ID."""
    try:
        index = CONTROL_IDS.index(control_id)
    except ValueError as exc:
        raise ValueError(f"unknown control ID {control_id!r}") from exc
    return GPIO_NAMES_BY_CONTROL[index]


def gpio_index_by_name(name: str) -> int:
    """Return the zero-based harness index for one frozen GPIO name."""
    try:
        return GPIO_NAMES_BY_CONTROL.index(name)
    except ValueError as exc:
        raise ValueError(f"unknown GPIO name {name!r}") from exc


def is_active_low_pressed(raw_pin_value: object) -> bool:
    """Decode a pulled-up GPIO reading (LOW means pressed)."""
    if raw_pin_value is not True and raw_pin_value is not False:
        raise ValueError(f"raw GPIO value must be bool, got {raw_pin_value!r}")
    return raw_pin_value is False


def control_states_from_raw(raw_pin_values: "tuple[object, ...]") -> "dict[str, bool]":
    """Decode exactly six raw GPIO values into semantic control states.

    The length and value checks deliberately fail closed.  A malformed
    harness sample must not become a plausible released controller.
    """
    if len(raw_pin_values) != CONTROL_COUNT:
        raise ValueError(
            f"raw GPIO sample must contain exactly {CONTROL_COUNT} values, "
            f"got {len(raw_pin_values)}"
        )
    states: "dict[str, bool]" = {}  # noqa: UP037 — avoid PEP 585 evaluation on-device
    for control_id, raw_value in zip(CONTROL_IDS, raw_pin_values):  # noqa: B905
        states[control_id] = is_active_low_pressed(raw_value)
    return states


__all__ = [
    "CONTROL_COUNT",
    "CONTROL_IDS",
    "CONTROL_ROLES",
    "GPIO_NAMES_BY_CONTROL",
    "control_states_from_raw",
    "control_to_gpio_name",
    "gpio_index_by_name",
    "is_active_low_pressed",
]
