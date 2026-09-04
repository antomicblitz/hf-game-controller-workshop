"""GPIO sampling for the active exact-six controller harness.

This module intentionally contains no bus, expander, or external peripheral
path.  The runtime samples the six Feather ``DigitalInOut`` values, decodes
their active-low polarity, and hands semantic IDs to the HID composer.
"""

from .gpio_map import CONTROL_COUNT, CONTROL_IDS, control_states_from_raw

# The production loop samples at 100 Hz.  This is a deadline target; the
# scheduler still catches up if one sample takes longer than the target.
POLL_PERIOD_S = 0.010
POLL_HZ = 100


def decode_control_states(raw_pin_values: "tuple[object, ...]") -> "dict[str, bool]":
    """Decode one exact-six tuple of raw HIGH/LOW GPIO readings."""
    return control_states_from_raw(raw_pin_values)


def _pin_value(pin: object) -> bool:
    """Read one bool value from a DigitalInOut-like object."""
    value = getattr(pin, "value", None)
    if not isinstance(value, bool):
        raise ValueError(f"GPIO pin value must be bool, got {value!r}")
    return value


def read_control_states(pins: "list[object]") -> "dict[str, bool]":
    """Read and decode exactly six ``DigitalInOut``-like pins.

    ``pins`` is deliberately duck-typed so this module remains host-importable
    without CircuitPython.  A missing or extra pin is a harness error.
    """
    if len(pins) != CONTROL_COUNT:
        raise ValueError(
            f"controller must expose exactly {CONTROL_COUNT} GPIO pins, got {len(pins)}"
        )
    raw_pin_values: "tuple[bool, ...]" = tuple(  # noqa: UP037 — device-safe annotation
        _pin_value(pin) for pin in pins
    )
    return decode_control_states(raw_pin_values)


def all_released() -> "dict[str, bool]":
    """Return the semantic idle state for all six controls."""
    return {control_id: False for control_id in CONTROL_IDS}


__all__ = [
    "POLL_HZ",
    "POLL_PERIOD_S",
    "all_released",
    "decode_control_states",
    "read_control_states",
]
