"""CircuitPython runtime for the active exact-six GPIO controller.

Only this module imports CircuitPython-only modules.  The runtime has six
normally-open inputs, all configured with internal pull-ups, and publishes a
four-byte HID report whenever the debounced semantic state changes.
"""

from .debounce import Debouncer
from .gpio_map import CONTROL_IDS, GPIO_NAMES_BY_CONTROL
from .hid_report import compute_hid_report
from .polling import POLL_PERIOD_S, read_control_states
from .serialization import HID_REPORT_BYTES, serialize_hid_report

MODE_NORMAL = "NORMAL"
HID_REPORT_ID = 0


class _DigitalPin:
    value: bool
    direction: object
    pull: object


class _DigitalioModule:
    class Direction:
        INPUT: object

    class Pull:
        UP: object

    def DigitalInOut(self, pin: object) -> _DigitalPin: ...


class _BoardModule:
    pass


class _UsbDevice:
    report_descriptor: bytes
    usage_page: int
    usage: int
    report_ids: "tuple[int, ...]"
    in_report_lengths: "tuple[int, ...]"
    out_report_lengths: "tuple[int, ...]"

    def send_report(self, payload: bytes) -> None: ...


class _DeviceCollection:
    def __len__(self) -> int: ...

    def __getitem__(self, index: int) -> _UsbDevice: ...


class _UsbHidModule:
    devices: _DeviceCollection

    def Device(self, **kwargs: object) -> _UsbDevice: ...

    def enable(self, devices: object) -> None: ...


class _PublishState:
    pins: "list[object]"
    debouncers: "list[Debouncer]"
    dt_s: float
    last_payload: object

    def __init__(self, pins: "list[object]") -> None:
        self.pins = pins
        self.debouncers = [Debouncer() for _ in pins]
        self.dt_s = POLL_PERIOD_S
        self.last_payload = None


class FirmwareError(RuntimeError):
    """Raised when CircuitPython libraries, USB setup, or wiring is invalid."""


def _import_circuitpython_libs():
    """Import the CircuitPython core modules required by the active path."""
    try:
        import board  # pyright: ignore[reportMissingImports]
        import digitalio  # pyright: ignore[reportMissingImports]
        import usb_hid  # pyright: ignore[reportMissingImports]
    except ImportError as exc:
        raise FirmwareError(
            "CircuitPython standard library import failed. "
            "Verify the firmware is running on an Adafruit Feather nRF52840 Sense "
            "(PID 4516): " + str(exc)
        ) from exc

    board_mod: _BoardModule = board  # pyright: ignore[reportAssignmentType]
    digitalio_mod: _DigitalioModule = digitalio  # pyright: ignore[reportAssignmentType]
    usb_hid_mod: _UsbHidModule = usb_hid  # pyright: ignore[reportAssignmentType]
    return board_mod, digitalio_mod, usb_hid_mod


def build_gpio_pins(
    digitalio_mod: object, board_mod: object, gpio_names: "tuple[str, ...]"
) -> "list[object]":
    """Configure the exact six GPIO inputs with internal pull-ups."""
    typed_digitalio: _DigitalioModule = digitalio_mod  # pyright: ignore[reportAssignmentType]
    pins: "list[object]" = []  # noqa: UP037 — avoid PEP 585 evaluation on-device
    if gpio_names != GPIO_NAMES_BY_CONTROL:
        raise FirmwareError("GPIO map does not match the exact-six contract: " + repr(gpio_names))
    for name in gpio_names:
        pin = typed_digitalio.DigitalInOut(getattr(board_mod, name))
        pin.direction = typed_digitalio.Direction.INPUT
        pin.pull = typed_digitalio.Pull.UP
        pins.append(pin)
    return pins


def resolve_mode(mode: str) -> str:
    """Accept only the production runtime mode."""
    if mode == MODE_NORMAL:
        return mode
    raise FirmwareError(f"unknown HF_FW_MODE {mode!r}; expected {MODE_NORMAL!r}")


def _publish_report(state: _PublishState, gamepad: object) -> bytes:
    """Sample, debounce, and publish one exact-six HID state."""
    typed_gamepad: _UsbDevice = gamepad  # pyright: ignore[reportAssignmentType]
    sampled_states = read_control_states(state.pins)
    stable_states: "dict[str, bool]" = {}  # noqa: UP037 — device-safe annotation
    for index, control_id in enumerate(CONTROL_IDS):
        stable_states[control_id] = state.debouncers[index].update(
            sampled_states[control_id], state.dt_s
        )

    payload = serialize_hid_report(compute_hid_report(stable_states))
    if payload != state.last_payload:
        typed_gamepad.send_report(payload)
        state.last_payload = payload
    return payload


def _run_normal_hid_loop() -> object:
    """Run the production GPIO → debounce → HID loop at 100 Hz."""
    from .scheduler import DeadlineScheduler

    board_mod, digitalio_mod, usb_hid_mod = _import_circuitpython_libs()
    pins = build_gpio_pins(digitalio_mod, board_mod, GPIO_NAMES_BY_CONTROL)
    if len(usb_hid_mod.devices) != 1:
        raise FirmwareError(
            "expected exactly one usb_hid device enabled by boot.py, found "
            + str(len(usb_hid_mod.devices))
        )
    gamepad = usb_hid_mod.devices[0]
    state = _PublishState(pins)

    def on_tick(dt_s: float) -> None:
        state.dt_s = dt_s
        _publish_report(state, gamepad)

    scheduler = DeadlineScheduler(POLL_PERIOD_S, on_tick)
    while True:
        scheduler.tick()


def main(mode: str = MODE_NORMAL) -> object:
    """Validate ``mode`` and start the production loop."""
    resolve_mode(mode)
    return _run_normal_hid_loop()


def make_gamepad_device(usb_hid_mod: object) -> _UsbDevice:
    """Construct the four-byte USB HID gamepad descriptor."""
    typed_usb_hid: _UsbHidModule = usb_hid_mod  # pyright: ignore[reportAssignmentType]
    try:
        return typed_usb_hid.Device(
            report_descriptor=bytes(_GAMEPAD_REPORT_DESCRIPTOR),
            usage_page=0x01,
            usage=0x05,
            report_ids=(HID_REPORT_ID,),
            in_report_lengths=(HID_REPORT_BYTES,),
            out_report_lengths=(0,),
        )
    except (OSError, ValueError) as exc:
        raise FirmwareError("Could not construct the USB HID gamepad device: " + str(exc)) from exc


def enable_hid_device(usb_hid_mod: object, device: object) -> None:
    """Enable the gamepad before USB enumeration completes."""
    typed_usb_hid: _UsbHidModule = usb_hid_mod  # pyright: ignore[reportAssignmentType]
    try:
        typed_usb_hid.enable((device,))
    except (OSError, ValueError) as exc:
        raise FirmwareError("usb_hid.enable() rejected the gamepad device: " + str(exc)) from exc


# Two signed axes followed by a sixteen-button mask.  Buttons 1 and 2 are used by
# the active exact-six contract; the wider descriptor keeps the USB shape
# stable for host tooling without creating extra physical inputs.
_GAMEPAD_REPORT_DESCRIPTOR = (
    0x05,
    0x01,
    0x09,
    0x05,
    0xA1,
    0x01,
    0x09,
    0x30,
    0x09,
    0x31,
    0x15,
    0x81,
    0x25,
    0x7F,
    0x75,
    0x08,
    0x95,
    0x02,
    0x81,
    0x02,
    0x05,
    0x09,
    0x19,
    0x01,
    0x29,
    0x10,
    0x15,
    0x00,
    0x25,
    0x01,
    0x75,
    0x01,
    0x95,
    0x10,
    0x81,
    0x02,
    0xC0,
)


__all__ = [
    "HID_REPORT_BYTES",
    "HID_REPORT_ID",
    "MODE_NORMAL",
    "FirmwareError",
    "build_gpio_pins",
    "enable_hid_device",
    "main",
    "make_gamepad_device",
    "resolve_mode",
]
