"""CircuitPython-safe exact-six controller firmware package.

The active controller is four PBS-33B cardinal buttons plus two GUUZI action
buttons.  Its six semantic controls are sampled from D5, D6, D9, D10, D11,
and D12, decoded active-low with internal pull-ups, and composed into a USB
HID report.  Host-only self-test code is intentionally not imported here.
"""

from . import debounce, gpio_map, hid_report, polling, scheduler, serialization
from .circuitpython_app import (
    HID_REPORT_BYTES,
    MODE_NORMAL,
    FirmwareError,
    enable_hid_device,
    main,
    make_gamepad_device,
)
from .hid_report import HIDReport

__all__ = [
    "HID_REPORT_BYTES",
    "MODE_NORMAL",
    "FirmwareError",
    "HIDReport",
    "debounce",
    "enable_hid_device",
    "gpio_map",
    "hid_report",
    "main",
    "make_gamepad_device",
    "polling",
    "scheduler",
    "serialization",
]
