"""The checked-in CIRCUITPY folder is the exact device-safe firmware bundle."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEVICE_FILES = {
    "__init__.py",
    "circuitpython_app.py",
    "debounce.py",
    "gpio_map.py",
    "hid_report.py",
    "polling.py",
    "scheduler.py",
    "serialization.py",
}


def test_copy_ready_firmware_matches_validated_sources() -> None:
    bundle = ROOT / "firmware" / "CIRCUITPY"
    package = bundle / "firmware"
    assert {path.name for path in package.iterdir() if path.is_file()} == DEVICE_FILES
    assert {path.name for path in bundle.iterdir() if path.is_file()} == {"boot.py", "code.py"}
    for name in DEVICE_FILES:
        assert (package / name).read_bytes() == (ROOT / "tools" / "firmware" / name).read_bytes()


def test_copy_ready_entrypoints_enable_and_run_the_gamepad() -> None:
    bundle = ROOT / "firmware" / "CIRCUITPY"
    assert "firmware.enable_hid_device" in (bundle / "boot.py").read_text(encoding="utf-8")
    assert "firmware.main(firmware.MODE_NORMAL)" in (bundle / "code.py").read_text(encoding="utf-8")
