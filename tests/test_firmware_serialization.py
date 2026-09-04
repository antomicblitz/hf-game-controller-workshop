"""Tests for the four-byte exact-six HID payload."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SESSION = _HERE.parent
sys.path.insert(0, str(_SESSION / "tools"))

import pytest  # noqa: E402
from firmware.hid_report import HIDReport  # noqa: E402
from firmware.serialization import (  # noqa: E402
    HID_REPORT_BYTES,
    deserialize_hid_report,
    serialize_hid_report,
)


def test_hid_report_has_four_bytes() -> None:
    assert HID_REPORT_BYTES == 4


@pytest.mark.parametrize(
    "report,expected",
    [
        (HIDReport(0, 0, 0), b"\x00\x00\x00\x00"),
        (HIDReport(-127, 127, 1), b"\x81\x7f\x01\x00"),
        (HIDReport(127, -127, 0x0100), b"\x7f\x81\x00\x01"),
    ],
)
def test_serialize_uses_signed_axes_and_little_endian_buttons(
    report: HIDReport, expected: bytes
) -> None:
    assert serialize_hid_report(report) == expected


@pytest.mark.parametrize("length", [0, 3, 5])
def test_deserialize_requires_exact_report_length(length: int) -> None:
    with pytest.raises(ValueError, match="exactly 4 bytes"):
        deserialize_hid_report(bytes(length))


def test_serialize_deserialize_round_trip() -> None:
    report = HIDReport(-100, 100, 0xCAFE)
    assert deserialize_hid_report(serialize_hid_report(report)) == report
