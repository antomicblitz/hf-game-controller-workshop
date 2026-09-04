"""CLI entry point for the host logic self-test.

Run from the ``tools/`` directory so ``firmware/`` is on
``sys.path``::

    cd sessions/2026-09-04-05/tools
    python -m firmware

There is no ``tools.firmware`` identity; this module is reached as
``firmware.__main__`` and therefore does **not** trigger the
runpy "found in sys.modules after import of package" warning that
plagues ``python -m tools.firmware.self_test`` style invocations.

This module imports ``self_test``, which uses ``dataclasses`` and
``collections.abc`` — host-only concerns. ``self_test`` is never
imported by the package ``__init__.py`` and is therefore never
loaded on the CIRCUITPY device.
"""

import sys

from .self_test import run

if __name__ == "__main__":
    sys.exit(run())
