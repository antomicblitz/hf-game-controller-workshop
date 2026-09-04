"""Host contract tests for the CircuitPython deployment boundary."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SESSION = _HERE.parent
sys.path.insert(0, str(_SESSION / "tools"))

import pytest  # noqa: E402
from firmware.circuitpython_app import (  # noqa: E402
    HID_REPORT_BYTES,
    HID_REPORT_ID,
    FirmwareError,
    build_gpio_pins,
    enable_hid_device,
    make_gamepad_device,
    resolve_mode,
)
from firmware.gpio_map import GPIO_NAMES_BY_CONTROL  # noqa: E402

import firmware  # noqa: E402


@dataclass
class FakePin:
    pin_id: object
    direction: object | None = None
    pull: object | None = None
    value: bool = True


class FakeDirection:
    INPUT = "INPUT"


class FakePull:
    UP = "UP"


class FakeDigitalInOutClass:
    def __init__(self, parent: FakeDigitalInOutModule) -> None:
        self._parent = parent

    def __call__(self, pin_id: object) -> FakePin:
        pin = FakePin(pin_id)
        self._parent.pins.append(pin)
        return pin


def _new_pins() -> list[FakePin]:
    return []


def _new_pin_attrs() -> dict[str, str]:
    return {}


def _new_reports() -> list[bytes]:
    return []


@dataclass
class FakeDigitalInOutModule:
    Direction: object = field(default_factory=FakeDirection)
    Pull: object = field(default_factory=FakePull)
    pins: list[FakePin] = field(default_factory=_new_pins)
    DigitalInOut: FakeDigitalInOutClass | None = None

    def __post_init__(self) -> None:
        self.DigitalInOut = FakeDigitalInOutClass(self)


@dataclass
class FakeBoard:
    pin_attrs: dict[str, str] = field(default_factory=_new_pin_attrs)

    def __getattr__(self, name: str) -> object:
        try:
            return self.pin_attrs[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


@dataclass
class FakeDevice:
    report_descriptor: bytes = b""
    usage_page: int = 0
    usage: int = 0
    report_ids: tuple[int, ...] = ()
    in_report_lengths: tuple[int, ...] = ()
    out_report_lengths: tuple[int, ...] = ()
    reports: list[bytes] = field(default_factory=_new_reports)

    def send_report(self, payload: bytes) -> None:
        self.reports.append(payload)


class FakeUsbHid:
    def __init__(
        self,
        raise_on_enable: Exception | None = None,
        raise_on_device: Exception | None = None,
    ) -> None:
        self.devices: list[FakeDevice] = []
        self.enabled: list[tuple[object, ...]] = []
        self.raise_on_enable = raise_on_enable
        self.raise_on_device = raise_on_device

    def Device(
        self,
        report_descriptor: bytes,
        usage_page: int,
        usage: int,
        report_ids: tuple[int, ...],
        in_report_lengths: tuple[int, ...],
        out_report_lengths: tuple[int, ...],
    ) -> FakeDevice:
        if self.raise_on_device is not None:
            raise self.raise_on_device
        device = FakeDevice(
            report_descriptor,
            usage_page,
            usage,
            report_ids,
            in_report_lengths,
            out_report_lengths,
        )
        self.devices.append(device)
        return device

    def enable(self, devices: tuple[object, ...]) -> None:
        if self.raise_on_enable is not None:
            raise self.raise_on_enable
        self.enabled.append(devices)


def _board() -> FakeBoard:
    return FakeBoard({name: f"board.{name}" for name in GPIO_NAMES_BY_CONTROL})


def test_build_gpio_pins_configures_all_six_inputs_with_pullups() -> None:
    digitalio = FakeDigitalInOutModule()
    build_gpio_pins(digitalio, _board(), GPIO_NAMES_BY_CONTROL)
    assert [pin.pin_id for pin in digitalio.pins] == [
        f"board.{name}" for name in GPIO_NAMES_BY_CONTROL
    ]
    assert [(pin.direction, pin.pull) for pin in digitalio.pins] == [("INPUT", "UP")] * 6


def test_build_gpio_pins_rejects_a_non_frozen_map() -> None:
    with pytest.raises(FirmwareError, match="exact-six contract"):
        build_gpio_pins(FakeDigitalInOutModule(), _board(), ("D5", "D6"))


def test_make_gamepad_device_populates_four_byte_hid_contract() -> None:
    device = make_gamepad_device(FakeUsbHid())
    assert device.usage_page == 0x01
    assert device.usage == 0x05
    assert device.report_ids == (HID_REPORT_ID,)
    assert device.in_report_lengths == (HID_REPORT_BYTES,)
    assert device.out_report_lengths == (0,)
    assert (
        bytes(
            (
                0x05,
                0x09,  # Usage Page (Button)
                0x19,
                0x01,  # Usage Minimum (Button 1)
                0x29,
                0x10,  # Usage Maximum (Button 16)
                0x15,
                0x00,  # Logical Minimum 0
                0x25,
                0x01,  # Logical Maximum 1
                0x75,
                0x01,  # Report Size 1 bit
                0x95,
                0x10,  # Report Count 16 buttons
                0x81,
                0x02,
            )
        )
        in device.report_descriptor
    )


def test_package_exports_deploy_entrypoints() -> None:
    assert firmware.main is firmware.circuitpython_app.main
    assert firmware.make_gamepad_device is make_gamepad_device
    assert firmware.enable_hid_device is enable_hid_device


def test_make_gamepad_device_reports_constructor_failure() -> None:
    with pytest.raises(FirmwareError, match="Could not construct"):
        make_gamepad_device(FakeUsbHid(raise_on_device=OSError("rejected")))


def test_enable_hid_device_exposes_one_device() -> None:
    usb_hid = FakeUsbHid()
    device = make_gamepad_device(usb_hid)
    enable_hid_device(usb_hid, device)
    assert usb_hid.enabled == [(device,)]


def test_enable_hid_device_reports_stack_failure() -> None:
    with pytest.raises(FirmwareError, match=r"usb_hid\.enable"):
        enable_hid_device(FakeUsbHid(raise_on_enable=ValueError("bad")), object())


def test_resolve_mode_accepts_only_normal() -> None:
    assert resolve_mode(firmware.MODE_NORMAL) == firmware.MODE_NORMAL
    with pytest.raises(FirmwareError, match="unknown HF_FW_MODE"):
        resolve_mode("SELF_TEST")


def test_import_circuitpython_libs_reports_missing_core_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "board", None)
    with pytest.raises(FirmwareError, match="standard library import failed"):
        firmware.circuitpython_app._import_circuitpython_libs()  # type: ignore[reportPrivateUsage]
