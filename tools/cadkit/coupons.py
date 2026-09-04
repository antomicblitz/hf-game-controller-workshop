"""Non-production coupons for the exact-six controller components.

Coupons are inspection artifacts only.  They never provide production
evidence; a signed :class:`cadkit.parametric.FrozenControlGeometry` record is
still required by production mount helpers.
"""

from __future__ import annotations

from typing import cast

from build123d import Align, Box, BuildPart, Part

from . import parametric as _param


def cube_20mm_coupon() -> Part:
    """Return the canonical printer-qualification cube."""
    with BuildPart() as bp:
        Box(20.0, 20.0, 20.0, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return cast(Part, bp.part)


def _component_gauge_coupon(gauge_values: tuple[float, ...]) -> Part:
    """Fit one active component's gauge candidates on a bounded plate."""
    minimum_length = max(gauge_values) + 4.0 + (len(gauge_values) - 1) * 15.0
    return _param.control_cutout_gauge_plate(
        plate_length_mm=max(70.0, minimum_length),
        plate_width_mm=max(24.0, max(gauge_values) + 4.0),
        gauge_values=gauge_values,
    )


def pbs33b_directional_gauge_coupon() -> Part:
    """Return the non-production PBS-33B cutout gauge."""
    return _component_gauge_coupon(_param.PBS33B_CUTOUT_GAUGE_MM)


def guuzi_b09bmrdptn_action_gauge_coupon() -> Part:
    """Return the non-production GUUZI action cutout gauge."""
    return _component_gauge_coupon(_param.GUUZI_CUTOUT_GAUGE_MM)


pbs33b_gauge_coupon = pbs33b_directional_gauge_coupon
guuzi_action_gauge_coupon = guuzi_b09bmrdptn_action_gauge_coupon


def feather_keepout_coupon() -> Part:
    """Return the historical direct-mount keep-out visualization."""
    length, width, height = _param.FEATHER_KEEP_OUT_MM
    with BuildPart() as bp:
        Box(length, width, height, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return cast(Part, bp.part)


def pbs33b_directional_keepout_coupon() -> Part:
    """Return the provisional PBS-33B underside keep-out visualization."""
    return _param.control_keepout(
        component_id=_param.PBS33B_DIRECTIONAL_COMPONENT_ID,
        provisional=True,
    )


def guuzi_b09bmrdptn_action_keepout_coupon() -> Part:
    """Return the provisional GUUZI underside keep-out visualization."""
    return _param.control_keepout(
        component_id=_param.GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID,
        provisional=True,
    )


def pbs33b_terminal_keepout_coupon() -> Part:
    """Return the provisional PBS-33B terminal-envelope visualization."""
    return _param.control_terminal_keepout(
        component_id=_param.PBS33B_DIRECTIONAL_COMPONENT_ID,
        provisional=True,
    )


def guuzi_action_terminal_keepout_coupon() -> Part:
    """Return the provisional GUUZI terminal-envelope visualization."""
    return _param.control_terminal_keepout(
        component_id=_param.GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID,
        provisional=True,
    )


__all__ = [
    "cube_20mm_coupon",
    "feather_keepout_coupon",
    "guuzi_action_gauge_coupon",
    "guuzi_action_terminal_keepout_coupon",
    "guuzi_b09bmrdptn_action_gauge_coupon",
    "guuzi_b09bmrdptn_action_keepout_coupon",
    "pbs33b_directional_gauge_coupon",
    "pbs33b_directional_keepout_coupon",
    "pbs33b_gauge_coupon",
    "pbs33b_terminal_keepout_coupon",
]
