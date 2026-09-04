"""Enable the exact-six USB HID gamepad when CircuitPython boots."""

import usb_hid

import firmware


device = firmware.make_gamepad_device(usb_hid)
firmware.enable_hid_device(usb_hid, device)
