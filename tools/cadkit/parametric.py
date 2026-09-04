"""Parametric primitives for the exact-six game-controller workshop path.

The active controller has four PBS-33B directional controls and two GUUZI
B09BMRDPTN action controls.  Receiving-controlled dimensions are represented by
the immutable :class:`FrozenControlGeometry` record.  Prototype and gauge
helpers may use the explicitly provisional constants, while production mount
helpers require a signed record.

All dimensions are in millimetres.  Historical D-pad, button-label, and spare
control generators are intentionally not part of this module.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import build123d as _build123d
from build123d import (
    Align,
    Box,
    BuildPart,
    BuildSketch,
    Circle,
    Cylinder,
    Keep,
    Location,
    Locations,
    Mode,
    Part,
    Plane,
    Polygon,
    RectangleRounded,
    RegularPolygon,
    extrude,
    fillet,
)

from . import evidence as _evidence
from .exterior_design import ExteriorDesignSpec, build_exterior_body
from .snap_geometry import AUTHORITATIVE_SNAP_GEOMETRY as _AUTHORITATIVE_SNAP

_build123d_api: Any = cast(Any, _build123d)
_add_part = cast(Callable[[Part], None], _build123d_api.add)


# ---------------------------------------------------------------------------
# Case and Feather envelope
# ---------------------------------------------------------------------------
CASE_DEFAULT_MM: tuple[float, float, float] = (130.0, 90.0, 65.0)
CASE_MAX_MM: tuple[float, float, float] = (140.0, 90.0, 100.0)
GAMEPAD_WALL_THICKNESS_MM: float = _AUTHORITATIVE_SNAP.wall_thickness_mm

FEATHER_EAGLE_BRD_URL: str = (
    "https://github.com/adafruit/Adafruit-Feather-nRF52840-Sense-PCB/"
    "blob/afeb0acb67/Adafruit%20Feather%20nRF52840%20Sense.brd"
)
FEATHER_BOARD_OUTLINE_MM: tuple[float, float] = (50.8, 22.86)
FEATHER_SENSE_BOARD_MM: tuple[float, float, float] = (51.0, 23.0, 7.2)
FEATHER_BOSS_HEIGHT_MM: float = 3.0
FEATHER_HEADER_BODY_HEIGHT_MM: float = 8.5
FEATHER_TOP_CONNECTOR_CLEARANCE_MM: float = 10.0
FEATHER_KEEP_OUT_MM: tuple[float, float, float] = (
    57.0,
    29.0,
    FEATHER_BOSS_HEIGHT_MM
    + FEATHER_SENSE_BOARD_MM[2]
    + FEATHER_HEADER_BODY_HEIGHT_MM
    + FEATHER_TOP_CONNECTOR_CLEARANCE_MM,
)
FEATHER_NUT_ACROSS_FLATS_MM: float = 5.3
FEATHER_NUT_DEPTH_MM: float = 2.2
FEATHER_THREAD: str = "M2.5"
FEATHER_SCREW_HOLE_DIAMETER_MM: float = 2.5
FEATHER_MOUNT_POST_COUNT: int = 4
FEATHER_MOUNTING_HOLES_BR_MM: tuple[tuple[float, float], ...] = (
    (2.54, 2.54),
    (48.26, 2.54),
    (2.54, 20.32),
    (48.26, 20.32),
)
FEATHER_MOUNTING_HOLES_CENTRED_MM: tuple[tuple[float, float], ...] = tuple(
    (x - 25.4, y - 11.43) for x, y in FEATHER_MOUNTING_HOLES_BR_MM
)
USB_MICRO_B_CUTOUT_MM: tuple[float, float] = (14.0, 8.0)
USB_MICRO_B_CORNER_RADIUS_MM: float = 2.0
USB_REAR_WALL_MM: float = 3.0
LED_VIEW_HOLE_DIAMETER_MM: float = 3.0
RESET_TOOL_HOLE_DIAMETER_MM: float = 3.0


def feather_mounting_holes_centred_mm() -> tuple[tuple[float, float], ...]:
    """Return historical Feather mounting-hole coordinates in centred XY.

    The coordinates remain exported for compatibility; they are not an
    active breadboard retention interface.
    """
    return FEATHER_MOUNTING_HOLES_CENTRED_MM


# ---------------------------------------------------------------------------
# Exact-six control geometry
# ---------------------------------------------------------------------------
PBS33B_DIRECTIONAL_COMPONENT_ID: str = "pbs33b_directional"
GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID: str = "guuzi_b09bmrdptn_action"
PBS33B_COMPONENT_ID: str = PBS33B_DIRECTIONAL_COMPONENT_ID
GUUZI_ACTION_COMPONENT_ID: str = GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID
ACTIVE_CONTROL_COMPONENT_IDS: tuple[str, ...] = (
    PBS33B_DIRECTIONAL_COMPONENT_ID,
    PBS33B_DIRECTIONAL_COMPONENT_ID,
    PBS33B_DIRECTIONAL_COMPONENT_ID,
    PBS33B_DIRECTIONAL_COMPONENT_ID,
    GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID,
    GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID,
)

# This legacy default remains because the assembly adapter imports it while
# constructing its transitional placement object.  Active component geometry
# always comes from the component-specific record below.
BUTTON_MOUNTING_LAND_MM: tuple[float, float] = (20.0, 2.0)
BUTTON_MAX_LOCAL_PANEL_MM: float = 3.0

# The printed-top-shell fit check selected the PBS 12.4 mm aperture for all six
# controls. These remain unsigned receiving candidates for production.
PBS33B_CUTOUT_GAUGE_MM: tuple[float, ...] = (12.2, 12.3, 12.4, 12.5, 12.6, 12.7)
PBS33B_CUTOUT_PROVISIONAL_MM: float = 12.4
PBS33B_MOUNTING_LAND_PROVISIONAL_MM: tuple[float, float] = (18.0, 2.0)
PBS33B_UNDERSIDE_KEEPOUT_PROVISIONAL_MM: tuple[float, float, float] = (18.0, 18.0, 10.69)
PBS33B_TERMINAL_KEEPOUT_PROVISIONAL_MM: tuple[float, float, float] = (14.0, 5.83, 5.0)
GUUZI_CUTOUT_GAUGE_MM: tuple[float, ...] = PBS33B_CUTOUT_GAUGE_MM
GUUZI_CUTOUT_PROVISIONAL_MM: float = PBS33B_CUTOUT_PROVISIONAL_MM
GUUZI_MOUNTING_LAND_PROVISIONAL_MM: tuple[float, float] = (14.2, 2.0)
GUUZI_UNDERSIDE_KEEPOUT_PROVISIONAL_MM: tuple[float, float, float] = (14.2, 14.2, 21.5)
GUUZI_TERMINAL_KEEPOUT_PROVISIONAL_MM: tuple[float, float, float] = (8.0, 4.0, 6.0)
BUTTON_ROUTE_MIN_BEND_RADIUS_PROVISIONAL_MM: float = 10.0


def _finite_positive(value: float, label: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a finite positive number") from exc
    if isinstance(value, bool) or not math.isfinite(numeric) or numeric <= 0:
        raise ValueError(f"{label} must be a finite positive number")
    return numeric


def _positive_dimensions(
    value: tuple[float, float, float], label: str
) -> tuple[float, float, float]:
    if len(value) != 3:
        raise ValueError(f"{label} must contain three dimensions")
    result = tuple(_finite_positive(item, f"{label}[{index}]") for index, item in enumerate(value))
    return cast(tuple[float, float, float], result)


@dataclass(frozen=True)
class FrozenControlGeometry:
    """Receiving-signed dimensions for one active control family."""

    component_id: str
    cutout_diameter_mm: float
    mounting_land_diameter_mm: float
    mounting_land_thickness_mm: float
    underside_keepout_mm: tuple[float, float, float]
    terminal_keepout_mm: tuple[float, float, float]
    source: str
    signed_by: str
    signed_utc: str
    signed_receiving_row: str
    coupon_binding: str
    minimum_thread_length_mm: float | None = None
    terminal_route_min_bend_radius_mm: float | None = None
    bound_evidence: _evidence.FrozenEvidenceRef | None = field(
        default=None,
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if self.component_id not in {
            PBS33B_DIRECTIONAL_COMPONENT_ID,
            GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID,
        }:
            raise ValueError(f"unsupported active control component: {self.component_id!r}")
        gauges = (
            PBS33B_CUTOUT_GAUGE_MM
            if self.component_id == PBS33B_DIRECTIONAL_COMPONENT_ID
            else GUUZI_CUTOUT_GAUGE_MM
        )
        if self.cutout_diameter_mm not in gauges:
            raise ValueError(
                f"{self.component_id} cutout must be selected from its receiving gauge"
            )
        _finite_positive(self.cutout_diameter_mm, "cutout_diameter_mm")
        _finite_positive(self.mounting_land_diameter_mm, "mounting_land_diameter_mm")
        land_thickness = _finite_positive(
            self.mounting_land_thickness_mm, "mounting_land_thickness_mm"
        )
        if land_thickness > BUTTON_MAX_LOCAL_PANEL_MM:
            raise ValueError(
                f"mounting_land_thickness_mm cannot exceed {BUTTON_MAX_LOCAL_PANEL_MM} mm"
            )
        _positive_dimensions(self.underside_keepout_mm, "underside_keepout_mm")
        _positive_dimensions(self.terminal_keepout_mm, "terminal_keepout_mm")
        for value, label in (
            (self.minimum_thread_length_mm, "minimum_thread_length_mm"),
            (self.terminal_route_min_bend_radius_mm, "terminal_route_min_bend_radius_mm"),
        ):
            if value is not None:
                _finite_positive(value, label)
        for value, label in (
            (self.source, "source"),
            (self.signed_by, "signed_by"),
            (self.signed_utc, "signed_utc"),
            (self.signed_receiving_row, "signed_receiving_row"),
            (self.coupon_binding, "coupon_binding"),
        ):
            if not value or len(value) > 256:
                raise ValueError(f"FrozenControlGeometry {label} is required")

    @property
    def is_receiving_signed(self) -> bool:
        """Whether this record is bound to the signed button provenance gate."""
        evidence = self.bound_evidence
        return all(
            (
                self.source,
                self.signed_by,
                self.signed_utc,
                self.signed_receiving_row,
                self.coupon_binding,
                evidence is not None,
                evidence is not None and evidence.gate_name == "button",
                evidence is not None and evidence.signed_by == self.signed_by,
                evidence is not None and evidence.signed_utc == self.signed_utc,
            )
        )

    @classmethod
    def from_frozen_evidence(
        cls,
        evidence: _evidence.FrozenEvidenceRef | Path | str,
    ) -> FrozenControlGeometry:
        """Load component geometry only from a signed ``gates.button`` record."""
        reference = (
            evidence
            if isinstance(evidence, _evidence.FrozenEvidenceRef)
            else _evidence.FrozenEvidenceRef.load(evidence, gate_name="button")
        )
        if reference.gate_name != "button":
            raise _evidence.ProvenanceGateNotPassed(
                f"control geometry requires the 'button' gate, got {reference.gate_name!r}"
            )
        computed = reference.content.get("computed")
        if not isinstance(computed, Mapping):
            raise _evidence.ProvenanceShapeInvalid(
                "control geometry provenance is missing computed"
            )
        computed_map = cast(Mapping[str, Any], computed)
        payload = computed_map.get("control_geometry")
        if not isinstance(payload, Mapping):
            raise _evidence.ProvenanceShapeInvalid(
                "control geometry provenance is missing computed.control_geometry"
            )
        payload_map = cast(Mapping[str, Any], payload)
        required = {
            "component_id",
            "cutout_diameter_mm",
            "mounting_land_diameter_mm",
            "mounting_land_thickness_mm",
            "underside_keepout_mm",
            "terminal_keepout_mm",
            "minimum_thread_length_mm",
            "terminal_route_min_bend_radius_mm",
            "signed_receiving_row",
            "coupon_binding",
        }
        if set(payload_map) != required:
            raise _evidence.ProvenanceShapeInvalid(
                "computed.control_geometry has unknown or missing fields"
            )
        try:
            underside = _evidence_dimensions(
                payload_map["underside_keepout_mm"], "underside_keepout_mm"
            )
            terminal = _evidence_dimensions(
                payload_map["terminal_keepout_mm"], "terminal_keepout_mm"
            )
            return cls(
                component_id=_evidence_text(payload_map["component_id"], "component_id"),
                cutout_diameter_mm=_evidence_number(
                    payload_map["cutout_diameter_mm"], "cutout_diameter_mm"
                ),
                mounting_land_diameter_mm=_evidence_number(
                    payload_map["mounting_land_diameter_mm"], "mounting_land_diameter_mm"
                ),
                mounting_land_thickness_mm=_evidence_number(
                    payload_map["mounting_land_thickness_mm"], "mounting_land_thickness_mm"
                ),
                underside_keepout_mm=underside,
                terminal_keepout_mm=terminal,
                source=f"provenance:{reference.record_id}",
                signed_by=reference.signed_by,
                signed_utc=reference.signed_utc,
                signed_receiving_row=_evidence_text(
                    payload_map["signed_receiving_row"], "signed_receiving_row"
                ),
                coupon_binding=_evidence_text(payload_map["coupon_binding"], "coupon_binding"),
                minimum_thread_length_mm=_evidence_number(
                    payload_map["minimum_thread_length_mm"], "minimum_thread_length_mm"
                ),
                terminal_route_min_bend_radius_mm=_evidence_number(
                    payload_map["terminal_route_min_bend_radius_mm"],
                    "terminal_route_min_bend_radius_mm",
                ),
                bound_evidence=reference,
            )
        except (TypeError, ValueError) as exc:
            raise _evidence.ProvenanceShapeInvalid(
                f"computed.control_geometry is malformed: {exc}"
            ) from exc

    def revalidate(self) -> None:
        """Revalidate the bound bytes and every parsed control-geometry field."""
        if self.bound_evidence is None:
            raise _evidence.ProvenanceShapeInvalid(
                "control geometry is not bound to frozen provenance"
            )
        self.bound_evidence.revalidate()
        if self != type(self).from_frozen_evidence(self.bound_evidence):
            raise _evidence.EvidenceTamper(
                "control geometry fields no longer match the bound provenance record"
            )

    @property
    def underside_keep_out_mm(self) -> tuple[float, float, float]:
        """Spelling-compatible receiving terminology alias."""
        return self.underside_keepout_mm

    @property
    def terminal_keep_out_mm(self) -> tuple[float, float, float]:
        """Spelling-compatible receiving terminology alias."""
        return self.terminal_keepout_mm

    @property
    def wire_terminal_keepout_mm(self) -> tuple[float, float, float]:
        """Alias emphasizing that the exit envelope protects the harness."""
        return self.terminal_keepout_mm

    def to_dict(self) -> dict[str, Any]:
        """Return the bounded placement-artifact representation."""
        return {
            "component_id": self.component_id,
            "cutout_diameter_mm": self.cutout_diameter_mm,
            "mounting_land_diameter_mm": self.mounting_land_diameter_mm,
            "mounting_land_thickness_mm": self.mounting_land_thickness_mm,
            "underside_keepout_mm": list(self.underside_keepout_mm),
            "terminal_keepout_mm": list(self.terminal_keepout_mm),
            "source": self.source,
            "signed_by": self.signed_by,
            "signed_utc": self.signed_utc,
            "signed_receiving_row": self.signed_receiving_row,
            "coupon_binding": self.coupon_binding,
            "minimum_thread_length_mm": self.minimum_thread_length_mm,
            "terminal_route_min_bend_radius_mm": self.terminal_route_min_bend_radius_mm,
        }


def _evidence_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError(f"{label} must be a bounded non-empty string")
    return value


def _evidence_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite positive number")
    return _finite_positive(float(value), label)


def _evidence_dimensions(value: Any, label: str) -> tuple[float, float, float]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a three-value list")
    values = cast(list[Any], value)
    if len(values) != 3:
        raise ValueError(f"{label} must be a three-value list")
    return _positive_dimensions(cast(tuple[float, float, float], tuple(values)), label)


# ---------------------------------------------------------------------------
# Shared geometry helpers
# ---------------------------------------------------------------------------
def hex_pocket(*, across_flats_mm: float, depth_mm: float) -> Part:
    """Build a real flat-top hexagonal captive-nut pocket."""
    if across_flats_mm <= 0 or depth_mm <= 0:
        raise ValueError("across_flats_mm and depth_mm must be positive.")
    circumscribed = across_flats_mm / 2 / math.cos(math.pi / 6)
    with BuildPart() as bp:
        with BuildSketch(Plane.XY):
            RegularPolygon(radius=circumscribed, side_count=6)
        extrude(amount=depth_mm)
    return cast(Part, bp.part)


def hex_pocket_anti_rotation_diameter(across_flats_mm: float) -> float:
    """Return the vertex-to-vertex diameter for a hex pocket."""
    return across_flats_mm / math.cos(math.pi / 6)


def hex_pocket_anti_rotation_flat_to_flat(across_flats_mm: float) -> float:
    """Return the flat-to-flat distance for a hex pocket."""
    return across_flats_mm


def feather_cavity(
    board: str = "nrf52840_sense_pid4516",
    *,
    lateral_clearance_mm: float = 0.0,
    wall: float | None = None,
    usb_wall: float | None = None,
) -> Part:
    """Build the historical direct-mount PID 4516 Feather cavity.

    This compatibility API is not used by the active breadboard-backed
    example or editor and must not be treated as a production interface.
    """
    if board not in ("nrf52840_sense_pid4516", "nrf52840_sense"):
        raise ValueError(
            f"Feather variant {board!r}: only 'nrf52840_sense_pid4516' "
            "(legacy alias 'nrf52840_sense') is accepted."
        )
    _ = wall, usb_wall
    keep_l, keep_w, keep_h = FEATHER_KEEP_OUT_MM
    with BuildPart() as bp:
        Box(
            keep_l + 2 * lateral_clearance_mm,
            keep_w + 2 * lateral_clearance_mm,
            keep_h,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
    return cast(Part, bp.part)


def feather_mount_boss(
    *,
    position_xy: tuple[float, float],
    boss_height_mm: float = FEATHER_BOSS_HEIGHT_MM,
    screw_hole_diameter_mm: float = FEATHER_SCREW_HOLE_DIAMETER_MM,
    nut_across_flats_mm: float = FEATHER_NUT_ACROSS_FLATS_MM,
    nut_depth_mm: float = FEATHER_NUT_DEPTH_MM,
    thread: str = FEATHER_THREAD,
) -> Part:
    """Build one historical direct-mount Feather boss.

    Retained for parsing and legacy coupon compatibility only; the active
    electronics backbone is bonded directly to the inside bottom shell instead.
    """
    if thread != FEATHER_THREAD:
        raise ValueError(f"Feather thread must be {FEATHER_THREAD!r} (got {thread!r}).")
    if min(boss_height_mm, screw_hole_diameter_mm, nut_across_flats_mm, nut_depth_mm) <= 0:
        raise ValueError("boss and fastener dimensions must be positive.")
    if nut_depth_mm > boss_height_mm:
        raise ValueError("nut_depth_mm cannot exceed boss_height_mm")
    outer_r = hex_pocket_anti_rotation_diameter(nut_across_flats_mm) / 2 + 1.0
    circumscribed = hex_pocket_anti_rotation_diameter(nut_across_flats_mm) / 2
    x, y = position_xy
    with BuildPart() as bp:
        with Locations((x, y, 0)):
            Cylinder(outer_r, boss_height_mm, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((x, y, 0)):
            Cylinder(
                screw_hole_diameter_mm / 2,
                boss_height_mm,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
                mode=Mode.SUBTRACT,
            )
        with Locations((x, y, boss_height_mm - nut_depth_mm)):
            with BuildSketch(Plane.XY):
                RegularPolygon(radius=circumscribed, side_count=6)
            extrude(amount=nut_depth_mm, mode=Mode.SUBTRACT)
    return cast(Part, bp.part)


def feather_mount_assembly(
    *,
    boss_height_mm: float = FEATHER_BOSS_HEIGHT_MM,
    screw_hole_diameter_mm: float = FEATHER_SCREW_HOLE_DIAMETER_MM,
    nut_across_flats_mm: float = FEATHER_NUT_ACROSS_FLATS_MM,
    nut_depth_mm: float = FEATHER_NUT_DEPTH_MM,
    thread: str = FEATHER_THREAD,
) -> Part:
    """Build the historical four-boss direct-mount assembly.

    This function remains an exported compatibility API but is not part of
    the active breadboard-backed controller path.
    """
    if thread != FEATHER_THREAD:
        raise ValueError(f"thread must be {FEATHER_THREAD!r}.")
    with BuildPart() as bp:
        for position in feather_mounting_holes_centred_mm():
            _add_part(
                feather_mount_boss(
                    position_xy=position,
                    boss_height_mm=boss_height_mm,
                    screw_hole_diameter_mm=screw_hole_diameter_mm,
                    nut_across_flats_mm=nut_across_flats_mm,
                    nut_depth_mm=nut_depth_mm,
                    thread=thread,
                )
            )
    return cast(Part, bp.part)


def usb_cutout(
    feather_variant: str = "nrf52840_sense_pid4516",
    *,
    width_mm: float = USB_MICRO_B_CUTOUT_MM[0],
    height_mm: float = USB_MICRO_B_CUTOUT_MM[1],
    corner_radius_mm: float = USB_MICRO_B_CORNER_RADIUS_MM,
    margin_mm: float = 0.0,
) -> Part:
    """Build the rounded PID 4516 Micro-B opening."""
    if feather_variant not in ("nrf52840_sense_pid4516", "nrf52840_sense"):
        raise ValueError("only 'nrf52840_sense_pid4516' is accepted")
    with BuildPart() as bp:
        with BuildSketch(Plane.XY):
            RectangleRounded(width_mm + 2 * margin_mm, height_mm + 2 * margin_mm, corner_radius_mm)
        extrude(amount=4.0)
    return cast(Part, bp.part)


def status_led_view_hole(diameter_mm: float = LED_VIEW_HOLE_DIAMETER_MM) -> Part:
    """Build the Feather status-LED through-hole."""
    if diameter_mm <= 0:
        raise ValueError("diameter_mm must be positive.")
    with BuildPart() as bp:
        Cylinder(diameter_mm / 2, 3.0, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return cast(Part, bp.part)


def reset_tool_hole(diameter_mm: float = RESET_TOOL_HOLE_DIAMETER_MM) -> Part:
    """Build the Feather reset-button tool hole."""
    if diameter_mm <= 0:
        raise ValueError("diameter_mm must be positive.")
    with BuildPart() as bp:
        Cylinder(diameter_mm / 2, 3.0, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return cast(Part, bp.part)


def _require_active_component(component_id: str) -> None:
    if component_id not in {
        PBS33B_DIRECTIONAL_COMPONENT_ID,
        GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID,
    }:
        raise ValueError(f"unsupported active control component: {component_id!r}")


def _provisional_control_values(
    component_id: str,
) -> tuple[
    float,
    tuple[float, float],
    tuple[float, float, float],
    tuple[float, float, float],
    float,
]:
    _require_active_component(component_id)
    if component_id == PBS33B_DIRECTIONAL_COMPONENT_ID:
        return (
            PBS33B_CUTOUT_PROVISIONAL_MM,
            PBS33B_MOUNTING_LAND_PROVISIONAL_MM,
            PBS33B_UNDERSIDE_KEEPOUT_PROVISIONAL_MM,
            PBS33B_TERMINAL_KEEPOUT_PROVISIONAL_MM,
            4.0,
        )
    return (
        GUUZI_CUTOUT_PROVISIONAL_MM,
        GUUZI_MOUNTING_LAND_PROVISIONAL_MM,
        GUUZI_UNDERSIDE_KEEPOUT_PROVISIONAL_MM,
        GUUZI_TERMINAL_KEEPOUT_PROVISIONAL_MM,
        7.5,
    )


def control_mount(*, component_id: str, geometry: FrozenControlGeometry) -> Part:
    """Build one production cutout from matching signed geometry."""
    _require_active_component(component_id)
    if type(geometry) is not FrozenControlGeometry:
        raise ValueError("control_mount requires a FrozenControlGeometry record")
    if geometry.component_id != component_id or not geometry.is_receiving_signed:
        raise ValueError("control_mount geometry evidence does not match the component")
    with BuildPart() as bp:
        Cylinder(
            geometry.cutout_diameter_mm / 2,
            geometry.underside_keepout_mm[2],
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
    return cast(Part, bp.part)


def control_mount_gauge(*, component_id: str) -> Part:
    """Build a non-production provisional cutout for one active component."""
    cutout, _land, underside, _terminal, _thread = _provisional_control_values(component_id)
    with BuildPart() as bp:
        Cylinder(cutout / 2, underside[2], align=(Align.CENTER, Align.CENTER, Align.MIN))
    return cast(Part, bp.part)


def _control_dimensions(
    component_id: str,
    *,
    geometry: FrozenControlGeometry | None,
    provisional: bool,
    field_name: str,
) -> tuple[float, float, float]:
    _require_active_component(component_id)
    if geometry is not None:
        if type(geometry) is not FrozenControlGeometry:
            raise ValueError(f"{field_name} requires a FrozenControlGeometry record")
        if geometry.component_id != component_id:
            raise ValueError("control geometry evidence does not match the component")
        if not provisional and not geometry.is_receiving_signed:
            raise ValueError(f"{field_name} requires receiving-signed geometry")
        return cast(tuple[float, float, float], getattr(geometry, field_name))
    if not provisional:
        raise ValueError(f"{field_name} requires matching receiving-signed geometry")
    _cutout, _land, underside, terminal, _thread = _provisional_control_values(component_id)
    return underside if field_name == "underside_keepout_mm" else terminal


def control_keepout(
    *,
    component_id: str,
    geometry: FrozenControlGeometry | None = None,
    provisional: bool = False,
) -> Part:
    """Build an active control's underside keep-out."""
    dimensions = _control_dimensions(
        component_id,
        geometry=geometry,
        provisional=provisional,
        field_name="underside_keepout_mm",
    )
    with BuildPart() as bp:
        Box(*dimensions, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return cast(Part, bp.part)


def control_terminal_keepout(
    *,
    component_id: str,
    geometry: FrozenControlGeometry | None = None,
    provisional: bool = False,
) -> Part:
    """Build an active control's insulated terminal envelope."""
    dimensions = _control_dimensions(
        component_id,
        geometry=geometry,
        provisional=provisional,
        field_name="terminal_keepout_mm",
    )
    with BuildPart() as bp:
        Box(*dimensions, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return cast(Part, bp.part)


def control_mounting_land(
    *,
    component_id: str,
    geometry: FrozenControlGeometry | None = None,
    provisional: bool = False,
) -> Part:
    """Build an active control's component-specific mounting land."""
    _require_active_component(component_id)
    if geometry is None and provisional:
        _cutout, land, _underside, _terminal, _thread = _provisional_control_values(component_id)
        diameter, thickness = land
    elif type(geometry) is FrozenControlGeometry and geometry.component_id == component_id:
        if not provisional and not geometry.is_receiving_signed:
            raise ValueError("control_mounting_land requires receiving-signed geometry")
        diameter = geometry.mounting_land_diameter_mm
        thickness = geometry.mounting_land_thickness_mm
    else:
        raise ValueError("control_mounting_land requires matching signed geometry")
    with BuildPart() as bp:
        Cylinder(diameter / 2, thickness, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return cast(Part, bp.part)


def pbs33b_directional_mount(*, geometry: FrozenControlGeometry) -> Part:
    """Build a signed PBS-33B directional cutout."""
    return control_mount(component_id=PBS33B_DIRECTIONAL_COMPONENT_ID, geometry=geometry)


def guuzi_b09bmrdptn_action_mount(*, geometry: FrozenControlGeometry) -> Part:
    """Build a signed GUUZI action cutout."""
    return control_mount(component_id=GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID, geometry=geometry)


def pbs33b_directional_keepout(*, geometry: FrozenControlGeometry) -> Part:
    """Build a signed PBS-33B underside keep-out."""
    return control_keepout(component_id=PBS33B_DIRECTIONAL_COMPONENT_ID, geometry=geometry)


def guuzi_b09bmrdptn_action_keepout(*, geometry: FrozenControlGeometry) -> Part:
    """Build a signed GUUZI underside keep-out."""
    return control_keepout(component_id=GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID, geometry=geometry)


pbs33b_mount = pbs33b_directional_mount
pbs33b_keepout = pbs33b_directional_keepout
guuzi_action_mount = guuzi_b09bmrdptn_action_mount
guuzi_action_keepout = guuzi_b09bmrdptn_action_keepout


def control_cutout_gauge_plate(
    *,
    plate_length_mm: float,
    plate_width_mm: float,
    plate_thickness_mm: float = 10.0,
    gauge_values: tuple[float, ...],
    hole_margin_mm: float = 2.0,
) -> Part:
    """Build a non-production plate containing one hole per gauge value."""
    if plate_length_mm <= 0 or plate_width_mm <= 0 or plate_thickness_mm <= 0:
        raise ValueError("plate dimensions must be positive.")
    if not gauge_values:
        raise ValueError("gauge_values must not be empty")
    if hole_margin_mm < 0:
        raise ValueError("hole_margin_mm must be ≥ 0.")
    max_hole = max(gauge_values)
    spacing_mm = 15.0
    min_length = max_hole + 2 * hole_margin_mm + (len(gauge_values) - 1) * spacing_mm
    if plate_length_mm < min_length:
        raise ValueError(f"plate_length_mm={plate_length_mm} is too short to enclose every hole")
    min_width = max_hole + 2 * hole_margin_mm
    if plate_width_mm < min_width:
        raise ValueError(f"plate_width_mm={plate_width_mm} is too narrow")
    first_centre = hole_margin_mm + max_hole / 2
    centres = tuple(first_centre + index * spacing_mm for index in range(len(gauge_values)))
    with BuildPart() as bp:
        Box(
            plate_length_mm,
            plate_width_mm,
            plate_thickness_mm,
            align=(Align.MIN, Align.CENTER, Align.MIN),
        )
        for centre, diameter in zip(centres, gauge_values, strict=True):
            with BuildSketch(Plane.XY.offset(0)), Locations((centre, 0)):
                Circle(radius=diameter / 2)
            extrude(amount=plate_thickness_mm, mode=Mode.SUBTRACT)
    return cast(Part, bp.part)


def captive_nut_pocket(*, thread: str, across_flats_mm: float, depth_mm: float) -> Part:
    """Build the historical M2.5 captive-nut hex pocket.

    The function remains available for legacy parsing/API compatibility only;
    direct Feather M2.5 retention is not active.
    """
    if thread != "M2.5":
        raise ValueError(f"Unknown active captive-nut thread {thread!r}; only 'M2.5' is approved.")
    return hex_pocket(across_flats_mm=across_flats_mm, depth_mm=depth_mm)


def fillet_edges(part: Part, radius: float = 2.0) -> Part:
    """Apply a uniform fillet to every edge, retaining the source on failure."""
    if radius < 0:
        raise ValueError("radius must be non-negative.")
    if radius == 0:
        return part
    try:
        return cast(Part, fillet(part.edges(), radius))
    except Exception:
        return part


SNAP_FIT_TOLERANCE_MM: float = 0.30
SNAP_CLEARANCE_CANDIDATES_MM: tuple[float, ...] = (0.25, 0.30, 0.35)


def _snap_root_gusset(
    *,
    x: float,
    inner_wall_y: float,
    inward_direction: float,
    z_split: float,
) -> Part:
    """Build the permanent 45-degree support beneath one snap root."""
    geometry = _AUTHORITATIVE_SNAP
    run = geometry.unsupported_projection_mm
    with BuildPart() as gusset:
        with BuildSketch(Plane.YZ.offset(x - geometry.clip_length_mm / 2)):
            Polygon(
                (inner_wall_y, z_split),
                (inner_wall_y + inward_direction * run, z_split),
                (inner_wall_y, z_split + geometry.gusset_rise_mm),
                # Build123d 0.9 otherwise centres these global YZ coordinates.
                align=(Align.NONE, Align.NONE),
            )
        extrude(amount=geometry.clip_length_mm)
    return cast(Part, gusset.part)


def _validate_snap_wall_attachment(
    part: Part,
    *,
    x_offsets: tuple[float, ...],
    inner_walls: tuple[tuple[float, float], ...],
    z_split: float,
) -> None:
    """Require the fixed 2 mm wall at every clip attachment footprint."""
    geometry = _AUTHORITATIVE_SNAP
    probe_offset = min(0.05, geometry.root_overlap_mm / 4)
    half_length = geometry.clip_length_mm / 2
    for x in x_offsets:
        x_probes = (x - half_length + probe_offset, x, x + half_length - probe_offset)
        for probe_x in x_probes:
            for inner_wall_y, inward_direction in inner_walls:
                wall_probe = (
                    probe_x,
                    inner_wall_y - inward_direction * probe_offset,
                    z_split + probe_offset,
                )
                cavity_probe = (
                    probe_x,
                    inner_wall_y + inward_direction * probe_offset,
                    z_split + probe_offset,
                )
                if not part.is_inside(wall_probe) or part.is_inside(cavity_probe):
                    raise ValueError(
                        "authoritative snap-fit requires the fixed 2 mm shell wall "
                        "at every clip attachment"
                    )


def snap_fit_pair(
    part: Part,
    *,
    tolerance_mm: float = SNAP_FIT_TOLERANCE_MM,
) -> tuple[Part, Part]:
    """Split a shell with fixed, self-supporting clips and qualified clearance.

    Clip, wall-overlap, and 45-degree gusset dimensions are intentionally not
    parameters. Generated case source and editor moves supply only the unsplit
    shell; authoritative editor/production paths apply this interface afterward.
    ``tolerance_mm`` remains variable because receiving selects it from a signed
    physical coupon record.
    """
    tolerance = _finite_positive(tolerance_mm, "tolerance_mm")
    if tolerance not in SNAP_CLEARANCE_CANDIDATES_MM:
        raise ValueError(
            f"tolerance_mm={tolerance} is not a receiving candidate {SNAP_CLEARANCE_CANDIDATES_MM}"
        )
    geometry = _AUTHORITATIVE_SNAP
    bb = part.bounding_box()
    z_split = bb.min.Z + min(
        bb.size.Z / 2.0,
        geometry.max_lower_shell_height_mm,
    )
    top = cast(Part | None, part.split(Plane.XY.offset(z_split), keep=Keep.TOP))
    bottom = cast(Part | None, part.split(Plane.XY.offset(z_split), keep=Keep.BOTTOM))
    if top is None or bottom is None:
        raise ValueError("part does not cross its authoritative split plane")
    x_offsets = tuple(bb.min.X + bb.size.X * fraction for fraction in geometry.x_fractions)
    half_depth = geometry.clip_depth_mm / 2
    y_specs = (
        (
            bb.min.Y + geometry.wall_thickness_mm + half_depth - geometry.root_overlap_mm,
            bb.min.Y + geometry.wall_thickness_mm,
            1.0,
        ),
        (
            bb.max.Y - geometry.wall_thickness_mm - half_depth + geometry.root_overlap_mm,
            bb.max.Y - geometry.wall_thickness_mm,
            -1.0,
        ),
    )
    _validate_snap_wall_attachment(
        part,
        x_offsets=x_offsets,
        inner_walls=tuple((inner_wall_y, direction) for _, inner_wall_y, direction in y_specs),
        z_split=z_split,
    )
    for x in x_offsets:
        for y, inner_wall_y, inward_direction in y_specs:
            clip = cast(
                Part,
                Location((x, y, z_split - geometry.clip_height_mm / 2))
                * Box(
                    geometry.clip_length_mm,
                    geometry.clip_depth_mm,
                    geometry.clip_height_mm,
                    align=(Align.CENTER, Align.CENTER, Align.CENTER),
                ),
            )
            gusset = _snap_root_gusset(
                x=x,
                inner_wall_y=inner_wall_y,
                inward_direction=inward_direction,
                z_split=z_split,
            )
            top = cast(Part, top + clip + gusset)
    for x in x_offsets:
        for y, _, _ in y_specs:
            slot = cast(
                Part,
                Location(
                    (
                        x,
                        y,
                        z_split - (geometry.clip_height_mm + tolerance) / 2,
                    )
                )
                * Box(
                    geometry.clip_length_mm + 2 * tolerance,
                    geometry.clip_depth_mm + 2 * tolerance,
                    geometry.clip_height_mm + tolerance,
                    align=(Align.CENTER, Align.CENTER, Align.CENTER),
                ),
            )
            bottom = cast(Part, bottom - slot)
    top_result, bottom_result = Part([top]), Part([bottom])
    if len(top_result.solids()) != 1 or len(bottom_result.solids()) != 1:
        raise ValueError("authoritative snap-fit geometry must produce one solid per shell half")
    return top_result, bottom_result


def orient_case_halves_for_print(top: Part, bottom: Part) -> tuple[Part, Part]:
    """Place both shell halves broad-face-down on the print bed.

    ``snap_fit_pair`` intentionally returns assembly-space geometry. This
    print-only transform flips the top 180 degrees around X and normalizes both
    halves to ``min.Z == 0`` so exported STL/STEP/BREP/3MF and slicer inputs can
    share one deterministic frame.
    """
    print_top = copy.deepcopy(top)
    print_bottom = copy.deepcopy(bottom)
    flipped_top = Part([Location((0.0, 0.0, 0.0), (180.0, 0.0, 0.0)) * print_top])
    top_on_bed = Part([Location((0.0, 0.0, -flipped_top.bounding_box().min.Z)) * flipped_top])
    bottom_on_bed = Part([Location((0.0, 0.0, -print_bottom.bounding_box().min.Z)) * print_bottom])
    return top_on_bed, bottom_on_bed


def gamepad_body(
    *,
    length_mm: float = CASE_DEFAULT_MM[0],
    width_mm: float = CASE_DEFAULT_MM[1],
    thickness_mm: float = CASE_DEFAULT_MM[2],
    palm_rest_angle_deg: float = 15.0,
    fillet_radius_mm: float = 2.0,
    feather_variant: str = "nrf52840_sense_pid4516",
    exterior_design: ExteriorDesignSpec | Mapping[str, Any] | None = None,
) -> Part:
    """Build the rounded, hollow shell body for the exact-six case."""
    length = _finite_positive(length_mm, "length_mm")
    width = _finite_positive(width_mm, "width_mm")
    thickness = _finite_positive(thickness_mm, "thickness_mm")
    wall_thickness = GAMEPAD_WALL_THICKNESS_MM
    if length > CASE_MAX_MM[0]:
        raise ValueError(f"length_mm={length} exceeds CASE_MAX_MM={CASE_MAX_MM[0]}.")
    if width > CASE_MAX_MM[1]:
        raise ValueError(f"width_mm={width} exceeds CASE_MAX_MM={CASE_MAX_MM[1]}.")
    if thickness > CASE_MAX_MM[2]:
        raise ValueError(f"thickness_mm={thickness} exceeds CASE_MAX_MM={CASE_MAX_MM[2]}.")
    if 2 * wall_thickness >= min(length, width, thickness):
        raise ValueError("case dimensions must leave a positive internal cavity")
    _ = palm_rest_angle_deg, feather_variant
    if exterior_design is not None:
        raw_fillet_radius: Any = fillet_radius_mm
        if isinstance(raw_fillet_radius, bool) or not isinstance(raw_fillet_radius, (int, float)):
            raise ValueError("fillet_radius_mm must be a finite non-negative number")
        if not math.isfinite(float(raw_fillet_radius)) or raw_fillet_radius < 0.0:
            raise ValueError("fillet_radius_mm must be a finite non-negative number")
        return build_exterior_body(
            exterior_design,
            length_mm=length,
            width_mm=width,
            thickness_mm=thickness,
            fillet_radius_mm=float(raw_fillet_radius),
        )
    with BuildPart() as outer_build:
        Box(length, width, thickness, align=(Align.CENTER, Align.CENTER, Align.MIN))
    outer = fillet_edges(cast(Part, outer_build.part), fillet_radius_mm)

    with BuildPart() as cavity_build, Locations((0.0, 0.0, wall_thickness)):
        Box(
            length - 2 * wall_thickness,
            width - 2 * wall_thickness,
            thickness - 2 * wall_thickness,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
    inner_radius = max(0.0, fillet_radius_mm - wall_thickness)
    cavity = fillet_edges(cast(Part, cavity_build.part), inner_radius)
    return cast(Part, outer - cavity)


LEGACY_FEATHER_VARIANT_PID4516 = "nrf52840_sense_pid4516"


__all__ = [
    "ACTIVE_CONTROL_COMPONENT_IDS",
    "BUTTON_MOUNTING_LAND_MM",
    "CASE_DEFAULT_MM",
    "CASE_MAX_MM",
    "FEATHER_EAGLE_BRD_URL",
    "FEATHER_HEADER_BODY_HEIGHT_MM",
    "FEATHER_KEEP_OUT_MM",
    "FEATHER_MOUNTING_HOLES_BR_MM",
    "FEATHER_MOUNTING_HOLES_CENTRED_MM",
    "FEATHER_NUT_ACROSS_FLATS_MM",
    "FEATHER_NUT_DEPTH_MM",
    "FEATHER_SCREW_HOLE_DIAMETER_MM",
    "FEATHER_SENSE_BOARD_MM",
    "FEATHER_THREAD",
    "GAMEPAD_WALL_THICKNESS_MM",
    "GUUZI_ACTION_COMPONENT_ID",
    "GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID",
    "GUUZI_CUTOUT_GAUGE_MM",
    "PBS33B_COMPONENT_ID",
    "PBS33B_CUTOUT_GAUGE_MM",
    "PBS33B_DIRECTIONAL_COMPONENT_ID",
    "RESET_TOOL_HOLE_DIAMETER_MM",
    "SNAP_CLEARANCE_CANDIDATES_MM",
    "SNAP_FIT_TOLERANCE_MM",
    "USB_MICRO_B_CORNER_RADIUS_MM",
    "USB_MICRO_B_CUTOUT_MM",
    "ExteriorDesignSpec",
    "FrozenControlGeometry",
    "captive_nut_pocket",
    "control_cutout_gauge_plate",
    "control_keepout",
    "control_mount",
    "control_mount_gauge",
    "control_mounting_land",
    "control_terminal_keepout",
    "feather_cavity",
    "feather_mount_assembly",
    "feather_mount_boss",
    "feather_mounting_holes_centred_mm",
    "fillet_edges",
    "gamepad_body",
    "guuzi_action_keepout",
    "guuzi_action_mount",
    "guuzi_b09bmrdptn_action_keepout",
    "guuzi_b09bmrdptn_action_mount",
    "hex_pocket",
    "hex_pocket_anti_rotation_diameter",
    "hex_pocket_anti_rotation_flat_to_flat",
    "orient_case_halves_for_print",
    "pbs33b_directional_keepout",
    "pbs33b_directional_mount",
    "pbs33b_keepout",
    "pbs33b_mount",
    "reset_tool_hole",
    "snap_fit_pair",
    "status_led_view_hole",
    "usb_cutout",
]
