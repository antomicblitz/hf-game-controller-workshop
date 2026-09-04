"""Host-side tests for the active exact-six GPIO runtime loop."""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

_HERE = Path(__file__).resolve().parent
_SESSION = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_SESSION / "tools"))

from firmware.gpio_map import GPIO_NAMES_BY_CONTROL  # noqa: E402
from test_firmware_deploy import (  # noqa: E402
    FakeBoard,
    FakeDevice,
    FakeDigitalInOutModule,
    FakePin,
    FakeUsbHid,
)

from firmware import circuitpython_app, scheduler  # noqa: E402


@dataclass
class RecordingDevice(FakeDevice):
    pass


class StopAfterTick:
    def __init__(self, _period_s: float, callback: object) -> None:
        self.callback = callback

    def tick(self) -> None:
        self.callback(0.020)  # type: ignore[reportCallIssue]
        self.callback(0.020)  # type: ignore[reportCallIssue]
        raise StopIteration


def _fake_modules(devices: list[FakeDevice]) -> dict[str, types.ModuleType]:
    board_fake = FakeBoard({name: f"board.{name}" for name in GPIO_NAMES_BY_CONTROL})
    digitalio_fake = FakeDigitalInOutModule()
    usb_hid_fake = FakeUsbHid()
    usb_hid_fake.devices = devices

    board_mod = types.ModuleType("board")
    board_mod.__dict__.update(board_fake.pin_attrs)

    digitalio_mod = types.ModuleType("digitalio")
    digitalio_mod.__dict__.update(
        {
            "Direction": digitalio_fake.Direction,
            "Pull": digitalio_fake.Pull,
            "DigitalInOut": digitalio_fake.DigitalInOut,
        }
    )

    usb_hid_mod = types.ModuleType("usb_hid")
    usb_hid_mod.__dict__.update(
        {
            "devices": usb_hid_fake.devices,
            "Device": usb_hid_fake.Device,
            "enable": usb_hid_fake.enable,
        }
    )
    return {"board": board_mod, "digitalio": digitalio_mod, "usb_hid": usb_hid_mod}


def test_runtime_publishes_both_active_low_actions_and_suppresses_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = RecordingDevice()
    modules = _fake_modules([device])
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    real_build_gpio_pins = circuitpython_app.build_gpio_pins

    def build_pressed_pins(
        digitalio_mod: object, board_mod: object, gpio_names: tuple[str, ...]
    ) -> list[object]:
        pins = cast(list[FakePin], real_build_gpio_pins(digitalio_mod, board_mod, gpio_names))
        pins[4].value = False
        pins[5].value = False
        return pins  # type: ignore[return-value]

    monkeypatch.setattr(circuitpython_app, "build_gpio_pins", build_pressed_pins)
    monkeypatch.setattr(scheduler, "DeadlineScheduler", StopAfterTick)
    with pytest.raises(StopIteration):
        circuitpython_app.main()

    assert device.reports == [b"\x00\x00\x03\x00"]


def test_runtime_requires_exactly_one_enabled_hid_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modules = _fake_modules([])
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    with pytest.raises(circuitpython_app.FirmwareError, match="exactly one usb_hid device"):
        circuitpython_app.main()
