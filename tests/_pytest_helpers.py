"""Typed wrappers for pytest APIs that are incomplete to Pyright."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any, Protocol, cast

import pytest


class _ApproxFactory(Protocol):
    def __call__(
        self,
        expected: Any,
        rel: float | Decimal | timedelta | None = None,
        abs: float | Decimal | timedelta | None = None,
        nan_ok: bool = False,
    ) -> Any: ...


def approx(
    expected: Any,
    rel: float | Decimal | timedelta | None = None,
    abs: float | Decimal | timedelta | None = None,
    nan_ok: bool = False,
) -> Any:
    """Call pytest.approx through its typed test boundary."""
    factory = cast(
        _ApproxFactory,
        pytest.approx,  # pyright: ignore[reportUnknownMemberType]
    )
    return factory(expected, rel=rel, abs=abs, nan_ok=nan_ok)
