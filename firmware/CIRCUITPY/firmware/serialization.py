"""HID gamepad report serialization.

The HID device on the Adafruit Feather nRF52840 Sense sends
4-byte gamepad reports over USB. The byte layout is fixed by the
device's HID report descriptor (see ``circuitpython_app.py``):

    offset 0  : signed X axis byte (LOGICAL_MIN/MAX -127..+127)
    offset 1  : signed Y axis byte (LOGICAL_MIN/MAX -127..+127)
    offset 2  : button mask, bits  0..7  (low byte, little-endian)
    offset 3  : button mask, bits 8..15  (high byte)

The descriptor reports an INPUT report length of 4 bytes, which
matches the size of the buffer this module produces.

Pure module
===========

No hardware imports. The deploy boundary in
``circuitpython_app.py`` packs the immutable report via
``Device.send_report(bytes_object)``; the byte layout is the
single contract between the host-testable core and the device.
Annotations are deliberately simple (no PEP 604 union, no PEP
585 subscripted generics) so the module imports cleanly on
CircuitPython 8.x regardless of whether the future-annotations
directive is honoured.
"""

from .hid_report import (
    ACTION_A_HID_BUTTON,
    ACTION_B_HID_BUTTON,
    BUTTON_MASK_BITS,
    HID_AXIS_MAX,
    HID_AXIS_MIN,
    HIDReport,
)

# CircuitPython evaluates function annotations at definition time; keep the
# accepted host-side input union in a static type comment instead.
# pyright: reportTypeCommentUsage=false

# Number of bytes in a single HID gamepad report. Must match the
# ``in_report_lengths`` tuple passed to ``usb_hid.Device``.
HID_REPORT_BYTES = 4


def serialize_hid_report(report: HIDReport) -> bytes:
    """Serialize one ``HIDReport`` to the canonical 4-byte buffer.

    The X/Y axes are converted to signed-byte form via two's-
    complement ``& 0xFF`` so -127 → 0x81 and +127 → 0x7F. The
    button mask is emitted little-endian: the low byte (bits
    0..7) comes first, the high byte (bits 8..15) second.

    Args:
        report: an ``HIDReport`` whose fields are already validated
            against the active exact-six HID contract.

    Returns:
        A 4-byte ``bytes`` object ready for ``Device.send_report``.
    """
    # Axes are constrained to [-127, +127] by HIDReport.__new__.
    # ``int & 0xFF`` yields the two's-complement byte form for
    # negative values (-127 → 0x81) and the literal byte for
    # non-negative values (+127 → 0x7F).
    x_byte = report.x & 0xFF
    y_byte = report.y & 0xFF
    low_byte = report.buttons & 0xFF
    high_byte = (report.buttons >> 8) & 0xFF
    return bytes((x_byte, y_byte, low_byte, high_byte))


def deserialize_hid_report(buffer):  # type: (bytes | bytearray | memoryview | tuple[int, ...]) -> HIDReport
    """Reconstruct an ``HIDReport`` from the canonical 4-byte buffer.

    Inverse of :func:`serialize_hid_report`. Used by the host-side
    contract tests and by any future parser that consumes the raw
    USB HID stream (e.g. a workshop diagnostic tool).

    The function accepts any object that supports ``len()`` and
    integer indexing — ``bytes``, ``bytearray``, ``memoryview``,
    or a tuple of integers. We avoid the PEP 604 union annotation
    (``bytes | bytearray | memoryview``) because we want this
    module to remain importable on CircuitPython without depending
    on PEP 604 syntax evaluation at function-definition time.

    Args:
        buffer: exactly 4 integer elements in [0, 255] (no padding,
            no report ID byte).

    Returns:
        The reconstructed ``HIDReport``.

    Raises:
        ValueError: if ``len(buffer) != HID_REPORT_BYTES``.
    """
    if len(buffer) != HID_REPORT_BYTES:
        raise ValueError(f"hid buffer must be exactly {HID_REPORT_BYTES} bytes, got {len(buffer)}")
    x_byte = buffer[0]
    y_byte = buffer[1]
    low_byte = buffer[2]
    high_byte = buffer[3]
    # Convert two's-complement signed bytes back to Python ints.
    x = x_byte - 0x100 if x_byte >= 0x80 else x_byte
    y = y_byte - 0x100 if y_byte >= 0x80 else y_byte
    buttons = low_byte | (high_byte << 8)
    return HIDReport(x=x, y=y, buttons=buttons)


__all__ = [
    # Re-exported for callers that import ``serialization`` only.
    "ACTION_A_HID_BUTTON",
    "ACTION_B_HID_BUTTON",
    "BUTTON_MASK_BITS",
    "HID_AXIS_MAX",
    "HID_AXIS_MIN",
    "HID_REPORT_BYTES",
    "HIDReport",
    "deserialize_hid_report",
    "serialize_hid_report",
]
