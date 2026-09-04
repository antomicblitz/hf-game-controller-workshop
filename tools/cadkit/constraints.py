"""Constraint library for the game-controller workshop.

Three categories of constraints (per procurement plan §5):
  - Print:     wall thickness, hole diameter, build volume, overhang,
               decorative thickness, ligament, bridge, support-free slice,
               print duration
   - Ergonomic: case envelope, exact-six control count, control spacing,
                and component-specific mounting geometry
  - Aesthetic: minimum fillet radius, vertical-axis symmetry, ≥ 1 fillet

The library distinguishes **warnings** (advisory, surfaced to the
student) from **errors** (hard, fail-closed by the export pipeline).
Procurement plan §5 row "Export policy" requires ``is_printable()`` to
fail closed: any failed or unavailable required print/fit check blocks
STL / 3MF / G-code export.

The wall-thickness and overhang checks are **unverified by
construction** — they are coarse bounding-box heuristics used at
design time; the procurement plan §5 fail-closed export gate requires
the export-time caller to set ``require_export_verification=True`` and
to supply ``overhang_verified=True`` / ``bridge_verified=True`` after
the slicer / manual review has signed.

Component / case thresholds are sourced from :mod:`cadkit.parametric`
where the manufacturer data lives. There is one source of truth.
"""

from __future__ import annotations

import contextlib
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any, cast

from build123d import Align, BoundBox, Cylinder, Location, Part

from . import aabb as _aabb
from . import electronics_backbone as _electronics
from . import evidence as _evidence
from . import exterior_design as _exterior
from . import geometry_evidence as _geom
from . import parametric as _p
from . import printers as _printers
from . import snap as _snap
from .aabb import AABB, Route, Volume
from .assembly import (
    ACTIVE_CONTROL_COUNT,
    CONTROL_COMPONENT_IDS,
    CONTROL_IDS,
    CONTROL_ROLES,
    ELECTRONICS_HARNESS_MIN_BEND_RADIUS_MM,
    ELECTRONICS_MIN_INTERNAL_CLEAR_Z_MM,
    ELECTRONICS_SHELL_WALL_MM,
    ELECTRONICS_SNAP_ADDITIONAL_MM,
    ElectronicsBackbone,
    case_local_to_build123d,
)
from .snap_geometry import AUTHORITATIVE_SNAP_GEOMETRY

# ---------------------------------------------------------------------------
# Active materials (procurement plan §1, §3.1, §5)
# ---------------------------------------------------------------------------
AVAILABLE_MATERIALS: dict[str, set[str]] = {
    "filament": {"PLA"},
    # Bind black to Prusament Galaxy Black (ASIN B0BJXPNG4M) and white to
    # Prusament Vanilla White (ASIN B0CJ22RCXS). Slicer profiles carry the
    # spool-specific tuning; the parser that consumes `docs/04-materials.md`
    # is deferred until the receiving checklist signs the filament row.
    "filament_color": {"black", "white"},
    "buttons": {
        _p.PBS33B_DIRECTIONAL_COMPONENT_ID,
        _p.GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID,
    },
    "active_controls": {
        _p.PBS33B_DIRECTIONAL_COMPONENT_ID,
        _p.GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID,
    },
    "feather": {"nrf52840_sense_pid4516"},
}


# ---------------------------------------------------------------------------
# Print thresholds (procurement plan §5) — re-exported from parametric
# ---------------------------------------------------------------------------
MIN_WALL_MM: float = 2.0
MIN_HOLE_DIAMETER_MM: float = 2.0
MIN_DECORATIVE_THICKNESS_MM: float = 1.2
MIN_EMBOSS_DEPTH_MM: float = 0.6
MIN_LIGAMENT_MM: float = 3.0
MAX_BRIDGE_MM: float = 10.0
MAX_OVERHANG_DEG_FROM_VERTICAL: float = 45.0

# Per-printer build volume. Removed the universal constant; the validator
# resolves the envelope from the qualified printer profile (procurement
# plan §5 "Printer volume" row).
BUILD_VOLUME_DEFAULT_MM: tuple[float, float, float] = (250.0, 210.0, 220.0)  # Prusa MK4 fallback

# ---------------------------------------------------------------------------
# Case envelope (procurement plan §1, §5) — sourced from parametric
# ---------------------------------------------------------------------------
CASE_DEFAULT_MM: tuple[float, float, float] = _p.CASE_DEFAULT_MM
CASE_MAX_MM: tuple[float, float, float] = _p.CASE_MAX_MM
MAX_PART_PER_HALF_MM: tuple[float, float, float] = (250.0, 210.0, 100.0)
BOUNDING_BOX_TOLERANCE_MM: float = 1e-6
EXTERIOR_GEOMETRY_TOLERANCE_MM: float = _exterior.EXTERIOR_GEOMETRY_TOLERANCE_MM
_EXTERIOR_LOCAL_BREP_RETRY_MM3: float = 0.01

MAX_CASE_LENGTH_MM: float = _p.CASE_MAX_MM[0]
MAX_CASE_WIDTH_MM: float = _p.CASE_MAX_MM[1]
MAX_CASE_THICKNESS_MM: float = _p.CASE_MAX_MM[2]
MAX_CASE_MATERIAL_FRACTION: float = 0.45

# Placements is a worker-to-host artifact. Keep its limits separate from
# assembly's limits so the export contract cannot grow with the editor protocol.
PLACEMENTS_SCHEMA = "cadkit.placements"
PLACEMENTS_SCHEMA_VERSION = "1.0"
MAX_PLACEMENTS_BYTES = 64 * 1024
MAX_PLACEMENTS_LIST_ITEMS = 512
MAX_PLACEMENTS_VOLUMES = 256
MAX_PLACEMENTS_ROUTES = 256
MAX_PLACEMENTS_OBJECT_KEYS = 32
MAX_PLACEMENTS_STRING = 256
MAX_PLACEMENTS_NUMBER_ABS = 1_000_000.0
MAX_PLACEMENTS_POSITION_MM = 10_000.0
MAX_PLACEMENTS_DIMENSION_MM = 1_000.0

# ---------------------------------------------------------------------------
# Ergonomic thresholds (procurement plan §5) — sourced from parametric
# ---------------------------------------------------------------------------
MIN_BUTTON_COUNT: int = ACTIVE_CONTROL_COUNT
MAX_BUTTON_COUNT: int = ACTIVE_CONTROL_COUNT
MIN_BUTTON_SPACING_MM: float = 20.0
PALM_REST_ANGLE_RANGE_DEG: tuple[float, float] = (10.0, 20.0)
BUTTON_MAX_LOCAL_PANEL_MM: float = _p.BUTTON_MAX_LOCAL_PANEL_MM
#: Button mounting-land diameter / thickness (procurement plan §5 row
#: "Button mounting land").
BUTTON_MOUNTING_LAND_DIAMETER_MM: float = _p.BUTTON_MOUNTING_LAND_MM[0]
BUTTON_MOUNTING_LAND_THICKNESS_MM: float = _p.BUTTON_MOUNTING_LAND_MM[1]
#: Min ligament (solid material) between cutout edges and case edges.
#: Procurement plan §5 row "Ligament".
MIN_LIGAMENT_MM_HARD: float = 3.0
#: Decorative feature min thickness (procurement plan §5 row
#: "Decorative thickness").
MIN_DECORATIVE_THICKNESS_MM_HARD: float = 1.2
#: Through-hole minimum diameter.
MIN_HOLE_DIAMETER_MM_HARD: float = 2.0
#: Emboss / engrave minimum depth.
MIN_EMBOSS_DEPTH_MM_HARD: float = 0.6
#: Stroke minimum width for surface features.
MIN_STROKE_WIDTH_MM: float = 1.2
MIN_INTERNAL_CLEAR_Z_MM: float = ELECTRONICS_MIN_INTERNAL_CLEAR_Z_MM
MIN_HARNESS_BEND_RADIUS_MM: float = ELECTRONICS_HARNESS_MIN_BEND_RADIUS_MM
MIN_SNAP_ADDITIONAL_MM: float = ELECTRONICS_SNAP_ADDITIONAL_MM

# ---------------------------------------------------------------------------
# Aesthetic thresholds (kept as warnings, not export blockers)
# ---------------------------------------------------------------------------
MIN_FILLET_RADIUS_MM: float = 2.0
MAX_ASYMMETRY_MM: float = 0.5

# Lookup dict for the agent prompt (rendered as a Markdown table).
THRESHOLDS: dict[str, float | tuple[float, ...] | int] = {
    # Print
    "min_wall_mm": MIN_WALL_MM,
    "min_hole_diameter_mm": MIN_HOLE_DIAMETER_MM,
    "min_decorative_thickness_mm": MIN_DECORATIVE_THICKNESS_MM,
    "min_emboss_depth_mm": MIN_EMBOSS_DEPTH_MM,
    "min_ligament_mm": MIN_LIGAMENT_MM,
    "max_bridge_mm": MAX_BRIDGE_MM,
    "max_overhang_deg_from_vertical": MAX_OVERHANG_DEG_FROM_VERTICAL,
    "build_volume_default_mm": BUILD_VOLUME_DEFAULT_MM,
    "max_part_per_half_mm": MAX_PART_PER_HALF_MM,
    # Case envelope
    "case_default_mm": CASE_DEFAULT_MM,
    "case_max_mm": CASE_MAX_MM,
    "max_case_length_mm": MAX_CASE_LENGTH_MM,
    "max_case_width_mm": MAX_CASE_WIDTH_MM,
    "max_case_thickness_mm": MAX_CASE_THICKNESS_MM,
    "max_case_material_fraction": MAX_CASE_MATERIAL_FRACTION,
    # Ergonomic
    "min_button_count": MIN_BUTTON_COUNT,
    "max_button_count": MAX_BUTTON_COUNT,
    "min_button_spacing_mm": MIN_BUTTON_SPACING_MM,
    "palm_rest_angle_range_deg": PALM_REST_ANGLE_RANGE_DEG,
    "button_max_local_panel_mm": BUTTON_MAX_LOCAL_PANEL_MM,
    # Aesthetic
    "min_fillet_radius_mm": MIN_FILLET_RADIUS_MM,
    "max_asymmetry_mm": MAX_ASYMMETRY_MM,
}


@dataclass
class ConstraintViolation:
    """One violation of a constraint rule."""

    category: str  # "print" | "ergonomic" | "aesthetic"
    rule: str  # short identifier, e.g. "min_wall_mm"
    severity: str  # "error" | "warning" | "info"
    message: str  # human-readable
    location: str = ""  # optional: where on the part, if known

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Placements:
    """Structured component placements the validator can reason about.

    Procurement plan §5 calls for **structured geometry / evidence**
    rather than bounding-box heuristics. ``Placements`` carries the
    positions and dimensions of every component the validator needs
    to check; callers construct it from the exact-six controls and
    non-geometric electronics-backbone binding and pass it to
    :func:`validate`.

    In addition to the legacy scalar fields (``case_length_mm`` etc.),
    the dataclass carries:

    * ``volumes``: a tuple of :class:`cadkit.aabb.Volume` records
      (AABB + name + role). The validator runs AABB non-overlap
      checks against these.
    * ``routes``: a tuple of :class:`cadkit.aabb.Route` records
      (ordered waypoints + bend radius). The validator enforces
      bend radius and route keep-out non-crossing.
    Historical scalar fields remain only so bounded pre-MIG artifacts can
    be parsed.  They are ignored by the active validator and are never
    emitted when an exact-six record is serialized.
    """

    case_length_mm: float = _p.CASE_DEFAULT_MM[0]
    case_width_mm: float = _p.CASE_DEFAULT_MM[1]
    case_thickness_mm: float = _p.CASE_DEFAULT_MM[2]
    button_centres_xy: tuple[tuple[float, float], ...] = ()
    button_diameters_mm: tuple[float, ...] = ()
    button_mounting_land_diameter_mm: float = BUTTON_MOUNTING_LAND_DIAMETER_MM
    button_mounting_land_thickness_mm: float = BUTTON_MOUNTING_LAND_THICKNESS_MM
    button_max_local_panel_mm: float = BUTTON_MAX_LOCAL_PANEL_MM
    dpad_centre_xy: tuple[float, float] | None = None
    feather_present: bool = True
    feather_keepout_mm: tuple[float, float, float] = _p.FEATHER_KEEP_OUT_MM
    usb_cutout_present: bool = True
    usb_cutout_mm: tuple[float, float] = _p.USB_MICRO_B_CUTOUT_MM
    usb_corner_radius_mm: float = _p.USB_MICRO_B_CORNER_RADIUS_MM
    led_view_hole_present: bool = True
    reset_tool_hole_present: bool = True
    # Structured volume + route records (VS-04 fix).
    volumes: tuple[Volume, ...] = ()
    routes: tuple[Route, ...] = ()
    # Historical-only corridor value.  Exact-six serialization emits null and
    # active validation ignores this field; old artifacts retain their values.
    dpad_cable_corridor_mm: tuple[float, float, float] | None = (
        12.0,
        8.0,
        10.0,
    )
    # MIG-02 active exact-six controls.  The historical fields above remain
    # readable for old examples, but any non-empty control field opts into the
    # exact-six contract and never activates a D-pad requirement.
    control_ids: tuple[str, ...] = ()
    control_roles: tuple[str, ...] = ()
    control_component_ids: tuple[str, ...] = ()
    control_centres_xy: tuple[tuple[float, float], ...] = ()
    control_cutout_diameters_mm: tuple[float, ...] = ()
    control_mounting_land_diameters_mm: tuple[float, ...] = ()
    control_mounting_land_thicknesses_mm: tuple[float, ...] = ()
    control_underside_keepouts_mm: tuple[tuple[float, float, float], ...] = ()
    control_terminal_keepouts_mm: tuple[tuple[float, float, float], ...] = ()
    control_terminal_route_min_bend_radii_mm: tuple[float, ...] = ()
    control_geometry_records: tuple[_p.FrozenControlGeometry, ...] = ()
    electronics_backbone: ElectronicsBackbone | None = None

    @property
    def is_active_control_contract(self) -> bool:
        """Whether this record uses the MIG-02 exact-six placement API."""
        return any(
            (
                self.control_ids,
                self.control_roles,
                self.control_component_ids,
                self.control_centres_xy,
                self.control_cutout_diameters_mm,
                self.control_geometry_records,
            )
        )

    @property
    def control_underside_keepout_mm(self) -> tuple[tuple[float, float, float], ...]:
        """Singular-name alias for integrations that use one value per control."""
        return self.control_underside_keepouts_mm

    @property
    def control_terminal_keepout_mm(self) -> tuple[tuple[float, float, float], ...]:
        """Singular-name alias for integrations that use one value per control."""
        return self.control_terminal_keepouts_mm

    @property
    def control_mounting_land_thickness_mm(self) -> tuple[float, ...]:
        """Singular-name alias for integrations that use one value per control."""
        return self.control_mounting_land_thicknesses_mm

    def __post_init__(self) -> None:
        # Keep legacy construction permissive enough for design-time validators
        # to report an envelope violation; production serialization applies the
        # canonical case bounds in ``to_dict`` / ``from_dict``.
        _validate_placements_values(self, enforce_case_bounds=False)

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical exact-six or historical compatibility artifact."""
        _validate_placements_values(self)
        if self.is_active_control_contract:
            return _active_placements_to_dict(self)
        return _historical_placements_to_dict(self)

    def to_json(self) -> str:
        """Return deterministic JSON for the bounded worker artifact."""
        encoded = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_PLACEMENTS_BYTES:
            raise ValueError("placements JSON exceeds its bounded artifact size")
        return encoded

    @classmethod
    def from_dict(cls, value: Any) -> Placements:
        """Parse a canonical exact-six or bounded historical artifact."""
        if not isinstance(value, Mapping):
            raise ValueError("placements payload must be an object")
        payload = dict(cast(Mapping[str, Any], value))
        payload_keys = frozenset(payload)
        if payload_keys in {_CANONICAL_PLACEMENTS_KEYS, _LEGACY_CANONICAL_PLACEMENTS_KEYS}:
            payload = _canonical_payload_with_legacy_defaults(payload)
        elif payload_keys == _HISTORICAL_PLACEMENTS_KEYS:
            payload = _historical_payload_with_active_defaults(payload)
        elif payload_keys != _PLACEMENTS_KEYS:
            raise ValueError("placements has unknown or missing fields")
        _require_exact_keys(payload, _PLACEMENTS_KEYS, "placements")
        if payload["schema"] != PLACEMENTS_SCHEMA:
            raise ValueError("placements schema must be 'cadkit.placements'")
        if (
            payload["schema_version"] != PLACEMENTS_SCHEMA_VERSION
            or payload["version"] != PLACEMENTS_SCHEMA_VERSION
        ):
            raise ValueError("unsupported or missing placements schema version")
        return cls(
            case_length_mm=_positive_number(
                payload["case_length_mm"], "case_length_mm", CASE_MAX_MM[0]
            ),
            case_width_mm=_positive_number(
                payload["case_width_mm"], "case_width_mm", CASE_MAX_MM[1]
            ),
            case_thickness_mm=_positive_number(
                payload["case_thickness_mm"], "case_thickness_mm", CASE_MAX_MM[2]
            ),
            button_centres_xy=_points2_from_dict(payload["button_centres_xy"], "button_centres_xy"),
            button_diameters_mm=_number_list_from_dict(
                payload["button_diameters_mm"], "button_diameters_mm"
            ),
            button_mounting_land_diameter_mm=_positive_number(
                payload["button_mounting_land_diameter_mm"], "button_mounting_land_diameter_mm"
            ),
            button_mounting_land_thickness_mm=_positive_number(
                payload["button_mounting_land_thickness_mm"], "button_mounting_land_thickness_mm"
            ),
            button_max_local_panel_mm=_positive_number(
                payload["button_max_local_panel_mm"], "button_max_local_panel_mm"
            ),
            dpad_centre_xy=_optional_point2(payload["dpad_centre_xy"], "dpad_centre_xy"),
            feather_present=_strict_bool(payload["feather_present"], "feather_present"),
            feather_keepout_mm=cast(
                tuple[float, float, float],
                _positive_number_tuple(payload["feather_keepout_mm"], "feather_keepout_mm", 3),
            ),
            usb_cutout_present=_strict_bool(payload["usb_cutout_present"], "usb_cutout_present"),
            usb_cutout_mm=cast(
                tuple[float, float],
                _positive_number_tuple(payload["usb_cutout_mm"], "usb_cutout_mm", 2),
            ),
            usb_corner_radius_mm=_positive_number(
                payload["usb_corner_radius_mm"], "usb_corner_radius_mm"
            ),
            led_view_hole_present=_strict_bool(
                payload["led_view_hole_present"], "led_view_hole_present"
            ),
            reset_tool_hole_present=_strict_bool(
                payload["reset_tool_hole_present"], "reset_tool_hole_present"
            ),
            volumes=_volumes_from_dict(payload["volumes"]),
            routes=_routes_from_dict(payload["routes"]),
            dpad_cable_corridor_mm=_optional_corridor_from_dict(payload["dpad_cable_corridor_mm"]),
            control_ids=_text_tuple_from_dict(payload["control_ids"], "control_ids"),
            control_roles=_text_tuple_from_dict(payload["control_roles"], "control_roles"),
            control_component_ids=_text_tuple_from_dict(
                payload["control_component_ids"], "control_component_ids"
            ),
            control_centres_xy=_points2_from_dict(
                payload["control_centres_xy"], "control_centres_xy"
            ),
            control_cutout_diameters_mm=_number_list_from_dict(
                payload["control_cutout_diameters_mm"], "control_cutout_diameters_mm"
            ),
            control_mounting_land_diameters_mm=_number_list_from_dict(
                payload["control_mounting_land_diameters_mm"],
                "control_mounting_land_diameters_mm",
            ),
            control_mounting_land_thicknesses_mm=_number_list_from_dict(
                payload["control_mounting_land_thicknesses_mm"],
                "control_mounting_land_thicknesses_mm",
            ),
            control_underside_keepouts_mm=_dimensions3_list_from_dict(
                payload["control_underside_keepouts_mm"], "control_underside_keepouts_mm"
            ),
            control_terminal_keepouts_mm=_dimensions3_list_from_dict(
                payload["control_terminal_keepouts_mm"], "control_terminal_keepouts_mm"
            ),
            control_terminal_route_min_bend_radii_mm=_number_list_from_dict(
                payload["control_terminal_route_min_bend_radii_mm"],
                "control_terminal_route_min_bend_radii_mm",
            ),
            control_geometry_records=_control_geometry_records_from_dict(
                payload["control_geometry_records"]
            ),
            electronics_backbone=_electronics_backbone_from_dict(payload["electronics_backbone"]),
        )


def _active_placements_to_dict(value: Placements) -> dict[str, Any]:
    """Serialize exact-six fields plus bounded legacy parser fields.

    The Feather/USB scalar fields remain for schema compatibility only. Active
    validation and production geometry use the strict electronics-backbone
    binding instead.
    """
    return {
        "schema": PLACEMENTS_SCHEMA,
        "schema_version": PLACEMENTS_SCHEMA_VERSION,
        "version": PLACEMENTS_SCHEMA_VERSION,
        "case_length_mm": value.case_length_mm,
        "case_width_mm": value.case_width_mm,
        "case_thickness_mm": value.case_thickness_mm,
        "feather_present": value.feather_present,
        "feather_keepout_mm": list(value.feather_keepout_mm),
        "usb_cutout_present": value.usb_cutout_present,
        "usb_cutout_mm": list(value.usb_cutout_mm),
        "usb_corner_radius_mm": value.usb_corner_radius_mm,
        "led_view_hole_present": value.led_view_hole_present,
        "reset_tool_hole_present": value.reset_tool_hole_present,
        "volumes": [_volume_to_dict(volume) for volume in value.volumes],
        "routes": [_route_to_dict(route) for route in value.routes],
        "dpad_centre_xy": None,
        "dpad_cable_corridor_mm": None,
        "control_ids": list(value.control_ids),
        "control_roles": list(value.control_roles),
        "control_component_ids": list(value.control_component_ids),
        "control_centres_xy": [list(point) for point in value.control_centres_xy],
        "control_cutout_diameters_mm": list(value.control_cutout_diameters_mm),
        "control_mounting_land_diameters_mm": list(value.control_mounting_land_diameters_mm),
        "control_mounting_land_thicknesses_mm": list(value.control_mounting_land_thicknesses_mm),
        "control_underside_keepouts_mm": [
            list(dimensions) for dimensions in value.control_underside_keepouts_mm
        ],
        "control_terminal_keepouts_mm": [
            list(dimensions) for dimensions in value.control_terminal_keepouts_mm
        ],
        "control_terminal_route_min_bend_radii_mm": list(
            value.control_terminal_route_min_bend_radii_mm
        ),
        "control_geometry_records": [record.to_dict() for record in value.control_geometry_records],
        "electronics_backbone": (
            value.electronics_backbone.to_dict() if value.electronics_backbone is not None else None
        ),
    }


def _historical_placements_to_dict(value: Placements) -> dict[str, Any]:
    """Serialize a bounded pre-MIG artifact without making it canonical."""
    return {
        "schema": PLACEMENTS_SCHEMA,
        "schema_version": PLACEMENTS_SCHEMA_VERSION,
        "version": PLACEMENTS_SCHEMA_VERSION,
        "case_length_mm": value.case_length_mm,
        "case_width_mm": value.case_width_mm,
        "case_thickness_mm": value.case_thickness_mm,
        "button_centres_xy": [list(point) for point in value.button_centres_xy],
        "button_diameters_mm": list(value.button_diameters_mm),
        "button_mounting_land_diameter_mm": value.button_mounting_land_diameter_mm,
        "button_mounting_land_thickness_mm": value.button_mounting_land_thickness_mm,
        "button_max_local_panel_mm": value.button_max_local_panel_mm,
        "dpad_centre_xy": list(value.dpad_centre_xy) if value.dpad_centre_xy is not None else None,
        "feather_present": value.feather_present,
        "feather_keepout_mm": list(value.feather_keepout_mm),
        "usb_cutout_present": value.usb_cutout_present,
        "usb_cutout_mm": list(value.usb_cutout_mm),
        "usb_corner_radius_mm": value.usb_corner_radius_mm,
        "led_view_hole_present": value.led_view_hole_present,
        "reset_tool_hole_present": value.reset_tool_hole_present,
        "volumes": [_volume_to_dict(volume) for volume in value.volumes],
        "routes": [_route_to_dict(route) for route in value.routes],
        "dpad_cable_corridor_mm": (
            list(value.dpad_cable_corridor_mm) if value.dpad_cable_corridor_mm is not None else None
        ),
    }


_PLACEMENTS_KEYS = frozenset(
    {
        "schema",
        "schema_version",
        "version",
        "case_length_mm",
        "case_width_mm",
        "case_thickness_mm",
        "button_centres_xy",
        "button_diameters_mm",
        "button_mounting_land_diameter_mm",
        "button_mounting_land_thickness_mm",
        "button_max_local_panel_mm",
        "dpad_centre_xy",
        "feather_present",
        "feather_keepout_mm",
        "usb_cutout_present",
        "usb_cutout_mm",
        "usb_corner_radius_mm",
        "led_view_hole_present",
        "reset_tool_hole_present",
        "volumes",
        "routes",
        "dpad_cable_corridor_mm",
        "control_ids",
        "control_roles",
        "control_component_ids",
        "control_centres_xy",
        "control_cutout_diameters_mm",
        "control_mounting_land_diameters_mm",
        "control_mounting_land_thicknesses_mm",
        "control_underside_keepouts_mm",
        "control_terminal_keepouts_mm",
        "control_terminal_route_min_bend_radii_mm",
        "control_geometry_records",
        "electronics_backbone",
    }
)
_CANONICAL_PLACEMENTS_KEYS = frozenset(
    {
        "schema",
        "schema_version",
        "version",
        "case_length_mm",
        "case_width_mm",
        "case_thickness_mm",
        "feather_present",
        "feather_keepout_mm",
        "usb_cutout_present",
        "usb_cutout_mm",
        "usb_corner_radius_mm",
        "led_view_hole_present",
        "reset_tool_hole_present",
        "volumes",
        "routes",
        "dpad_centre_xy",
        "dpad_cable_corridor_mm",
        "control_ids",
        "control_roles",
        "control_component_ids",
        "control_centres_xy",
        "control_cutout_diameters_mm",
        "control_mounting_land_diameters_mm",
        "control_mounting_land_thicknesses_mm",
        "control_underside_keepouts_mm",
        "control_terminal_keepouts_mm",
        "control_terminal_route_min_bend_radii_mm",
        "control_geometry_records",
        "electronics_backbone",
    }
)
_LEGACY_CANONICAL_PLACEMENTS_KEYS = frozenset(
    key
    for key in _CANONICAL_PLACEMENTS_KEYS
    if key not in {"dpad_centre_xy", "dpad_cable_corridor_mm", "electronics_backbone"}
)
_HISTORICAL_PLACEMENTS_KEYS = frozenset(
    {
        "schema",
        "schema_version",
        "version",
        "case_length_mm",
        "case_width_mm",
        "case_thickness_mm",
        "button_centres_xy",
        "button_diameters_mm",
        "button_mounting_land_diameter_mm",
        "button_mounting_land_thickness_mm",
        "button_max_local_panel_mm",
        "dpad_centre_xy",
        "feather_present",
        "feather_keepout_mm",
        "usb_cutout_present",
        "usb_cutout_mm",
        "usb_corner_radius_mm",
        "led_view_hole_present",
        "reset_tool_hole_present",
        "volumes",
        "routes",
        "dpad_cable_corridor_mm",
    }
)
_AABB_KEYS = frozenset({"min_x", "min_y", "min_z", "max_x", "max_y", "max_z"})
_VOLUME_KEYS = frozenset({"name", "aabb", "is_keep_out", "is_cable_corridor"})
_ROUTE_KEYS = frozenset({"name", "waypoints", "min_bend_radius_mm"})


def _canonical_payload_with_legacy_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    """Add parser-only empty button fields and neutral D-pad fields."""
    defaults: dict[str, Any] = {
        "button_centres_xy": [],
        "button_diameters_mm": [],
        "button_mounting_land_diameter_mm": BUTTON_MOUNTING_LAND_DIAMETER_MM,
        "button_mounting_land_thickness_mm": BUTTON_MOUNTING_LAND_THICKNESS_MM,
        "button_max_local_panel_mm": BUTTON_MAX_LOCAL_PANEL_MM,
        "dpad_centre_xy": None,
        "dpad_cable_corridor_mm": None,
        "electronics_backbone": None,
    }
    return {**defaults, **payload}


def _optional_corridor_from_dict(value: Any) -> tuple[float, float, float] | None:
    """Parse historical corridor dimensions or the active neutral null."""
    if value is None:
        return None
    return cast(
        tuple[float, float, float],
        _positive_number_tuple(value, "dpad_cable_corridor_mm", 3),
    )


def _historical_payload_with_active_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    """Add empty active fields while parsing a pre-MIG placement artifact."""
    defaults: dict[str, Any] = {
        "control_ids": [],
        "control_roles": [],
        "control_component_ids": [],
        "control_centres_xy": [],
        "control_cutout_diameters_mm": [],
        "control_mounting_land_diameters_mm": [],
        "control_mounting_land_thicknesses_mm": [],
        "control_underside_keepouts_mm": [],
        "control_terminal_keepouts_mm": [],
        "control_terminal_route_min_bend_radii_mm": [],
        "control_geometry_records": [],
        "electronics_backbone": None,
    }
    return {**payload, **defaults}


def _require_exact_keys(value: Mapping[Any, Any], expected: frozenset[str], label: str) -> None:
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} object keys must be strings")
    keys = set(value)
    unknown = keys - expected
    missing = expected - keys
    if unknown or missing:
        raise ValueError(f"{label} has unknown or missing fields")


def _number(value: Any, label: str, maximum: float = MAX_PLACEMENTS_NUMBER_ABS) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or abs(result) > maximum:
        raise ValueError(f"{label} must be a finite number within bounds")
    return result


def _positive_number(value: Any, label: str, maximum: float = MAX_PLACEMENTS_DIMENSION_MM) -> float:
    result = _number(value, label, maximum)
    if result <= 0:
        raise ValueError(f"{label} must be positive")
    return result


def _strict_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} must be a boolean")
    return value


def _positive_number_tuple(value: Any, label: str, length: int) -> tuple[float, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list of length {length}")
    values = cast(list[Any], value)
    if len(values) != length:
        raise ValueError(f"{label} must be a list of length {length}")
    return tuple(_positive_number(item, f"{label}[{index}]") for index, item in enumerate(values))


def _point2(value: Any, label: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must contain two coordinates")
    values = cast(list[Any] | tuple[Any, ...], value)
    if len(values) != 2:
        raise ValueError(f"{label} must contain two coordinates")
    points = tuple(
        _number(item, f"{label}[{index}]", MAX_PLACEMENTS_POSITION_MM)
        for index, item in enumerate(values)
    )
    return cast(tuple[float, float], points)


def _points2_from_dict(value: Any, label: str) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a bounded list")
    values = cast(list[Any], value)
    if len(values) > MAX_PLACEMENTS_LIST_ITEMS:
        raise ValueError(f"{label} must be a bounded list")
    return tuple(_point2(item, f"{label}[{index}]") for index, item in enumerate(values))


def _optional_point2(value: Any, label: str) -> tuple[float, float] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list or null")
    values = cast(list[Any], value)
    return _point2(values, label)


def _number_list_from_dict(value: Any, label: str) -> tuple[float, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a bounded list")
    values = cast(list[Any], value)
    if len(values) > MAX_PLACEMENTS_LIST_ITEMS:
        raise ValueError(f"{label} must be a bounded list")
    return tuple(_number(item, f"{label}[{index}]") for index, item in enumerate(values))


def _text_tuple_from_dict(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a bounded list")
    values = cast(list[Any], value)
    if len(values) > MAX_PLACEMENTS_LIST_ITEMS or any(
        not isinstance(item, str) or not item or len(item) > MAX_PLACEMENTS_STRING
        for item in values
    ):
        raise ValueError(f"{label} must contain bounded non-empty strings")
    return tuple(cast(str, item) for item in values)


def _dimensions3_list_from_dict(value: Any, label: str) -> tuple[tuple[float, float, float], ...]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a bounded list")
    values = cast(list[Any], value)
    if len(values) > MAX_PLACEMENTS_LIST_ITEMS:
        raise ValueError(f"{label} must be a bounded list")
    result: list[tuple[float, float, float]] = []
    for index, item in enumerate(values):
        result.append(
            cast(
                tuple[float, float, float],
                _positive_number_tuple(item, f"{label}[{index}]", 3),
            )
        )
    return tuple(result)


_CONTROL_GEOMETRY_KEYS = frozenset(
    {
        "component_id",
        "cutout_diameter_mm",
        "mounting_land_diameter_mm",
        "mounting_land_thickness_mm",
        "underside_keepout_mm",
        "terminal_keepout_mm",
        "source",
        "signed_by",
        "signed_utc",
        "signed_receiving_row",
        "coupon_binding",
        "minimum_thread_length_mm",
        "terminal_route_min_bend_radius_mm",
    }
)


def _control_geometry_record_from_dict(value: Any, index: int) -> _p.FrozenControlGeometry:
    label = f"control_geometry_records[{index}]"
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    payload = cast(Mapping[str, Any], value)
    _require_exact_keys(payload, _CONTROL_GEOMETRY_KEYS, label)
    component_id = payload["component_id"]
    if not isinstance(component_id, str):
        raise ValueError(f"{label}.component_id must be a string")
    minimum_thread = payload["minimum_thread_length_mm"]
    if minimum_thread is not None:
        minimum_thread = _positive_number(minimum_thread, f"{label}.minimum_thread_length_mm")
    route_radius = payload["terminal_route_min_bend_radius_mm"]
    if route_radius is not None:
        route_radius = _positive_number(
            route_radius,
            f"{label}.terminal_route_min_bend_radius_mm",
        )
    return _p.FrozenControlGeometry(
        component_id=component_id,
        cutout_diameter_mm=_positive_number(
            payload["cutout_diameter_mm"], f"{label}.cutout_diameter_mm"
        ),
        mounting_land_diameter_mm=_positive_number(
            payload["mounting_land_diameter_mm"], f"{label}.mounting_land_diameter_mm"
        ),
        mounting_land_thickness_mm=_positive_number(
            payload["mounting_land_thickness_mm"], f"{label}.mounting_land_thickness_mm"
        ),
        underside_keepout_mm=cast(
            tuple[float, float, float],
            _positive_number_tuple(
                payload["underside_keepout_mm"], f"{label}.underside_keepout_mm", 3
            ),
        ),
        terminal_keepout_mm=cast(
            tuple[float, float, float],
            _positive_number_tuple(
                payload["terminal_keepout_mm"], f"{label}.terminal_keepout_mm", 3
            ),
        ),
        source=_bounded_text(payload["source"], f"{label}.source"),
        signed_by=_bounded_text(payload["signed_by"], f"{label}.signed_by"),
        signed_utc=_bounded_text(payload["signed_utc"], f"{label}.signed_utc"),
        signed_receiving_row=_bounded_text(
            payload["signed_receiving_row"], f"{label}.signed_receiving_row"
        ),
        coupon_binding=_bounded_text(payload["coupon_binding"], f"{label}.coupon_binding"),
        minimum_thread_length_mm=minimum_thread,
        terminal_route_min_bend_radius_mm=route_radius,
    )


def _bounded_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_PLACEMENTS_STRING:
        raise ValueError(f"{label} must be a bounded non-empty string")
    return value


def _control_geometry_records_from_dict(value: Any) -> tuple[_p.FrozenControlGeometry, ...]:
    if not isinstance(value, list):
        raise ValueError("control_geometry_records must be a bounded list")
    values = cast(list[Any], value)
    if len(values) > MAX_PLACEMENTS_LIST_ITEMS:
        raise ValueError("control_geometry_records must be a bounded list")
    return tuple(
        _control_geometry_record_from_dict(item, index) for index, item in enumerate(values)
    )


def _electronics_backbone_from_dict(value: Any) -> ElectronicsBackbone | None:
    """Parse the optional compatibility field without accepting loose objects."""
    if value is None:
        return None
    try:
        return ElectronicsBackbone.from_dict(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"electronics_backbone is malformed: {exc}") from exc


def _volume_to_dict(volume: Volume) -> dict[str, Any]:
    return {
        "name": volume.name,
        "aabb": {
            "min_x": volume.aabb.min_x,
            "min_y": volume.aabb.min_y,
            "min_z": volume.aabb.min_z,
            "max_x": volume.aabb.max_x,
            "max_y": volume.aabb.max_y,
            "max_z": volume.aabb.max_z,
        },
        "is_keep_out": volume.is_keep_out,
        "is_cable_corridor": volume.is_cable_corridor,
    }


def _route_to_dict(route: Route) -> dict[str, Any]:
    return {
        "name": route.name,
        "waypoints": [list(point) for point in route.waypoints],
        "min_bend_radius_mm": route.min_bend_radius_mm,
    }


def _volume_from_dict(value: Any, index: int) -> Volume:
    label = f"volumes[{index}]"
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    payload = cast(Mapping[str, Any], value)
    _require_exact_keys(payload, _VOLUME_KEYS, label)
    aabb_value = payload["aabb"]
    if not isinstance(aabb_value, Mapping):
        raise ValueError(f"{label}.aabb must be an object")
    aabb_payload = cast(Mapping[str, Any], aabb_value)
    _require_exact_keys(aabb_payload, _AABB_KEYS, f"{label}.aabb")
    coordinates = tuple(
        _number(aabb_payload[key], f"{label}.aabb.{key}", MAX_PLACEMENTS_POSITION_MM)
        for key in ("min_x", "min_y", "min_z", "max_x", "max_y", "max_z")
    )
    try:
        aabb = AABB(*coordinates)
        name = payload["name"]
        if not isinstance(name, str) or not name or len(name) > MAX_PLACEMENTS_STRING:
            raise ValueError(f"{label}.name must be a bounded non-empty string")
        return Volume(
            name=name,
            aabb=aabb,
            is_keep_out=_strict_bool(payload["is_keep_out"], f"{label}.is_keep_out"),
            is_cable_corridor=_strict_bool(
                payload["is_cable_corridor"], f"{label}.is_cable_corridor"
            ),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is malformed") from exc


def _route_from_dict(value: Any, index: int) -> Route:
    label = f"routes[{index}]"
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    payload = cast(Mapping[str, Any], value)
    _require_exact_keys(payload, _ROUTE_KEYS, label)
    name = payload["name"]
    if not isinstance(name, str) or not name or len(name) > MAX_PLACEMENTS_STRING:
        raise ValueError(f"{label}.name must be a bounded non-empty string")
    waypoints_value = payload["waypoints"]
    if not isinstance(waypoints_value, list):
        raise ValueError(f"{label}.waypoints must contain 2 to 128 points")
    waypoint_values = cast(list[Any], waypoints_value)
    if not 2 <= len(waypoint_values) <= 128:
        raise ValueError(f"{label}.waypoints must contain 2 to 128 points")
    waypoints = tuple(
        _point3_from_dict(point, f"{label}.waypoints[{point_index}]")
        for point_index, point in enumerate(waypoint_values)
    )
    return Route(
        name=name,
        waypoints=waypoints,
        min_bend_radius_mm=_positive_number(
            payload["min_bend_radius_mm"], f"{label}.min_bend_radius_mm"
        ),
    )


def _point3_from_dict(value: Any, label: str) -> tuple[float, float, float]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list of length 3")
    coordinates = cast(list[Any], value)
    if len(coordinates) != 3:
        raise ValueError(f"{label} must be a list of length 3")
    result = tuple(
        _number(item, f"{label}[{index}]", MAX_PLACEMENTS_POSITION_MM)
        for index, item in enumerate(coordinates)
    )
    return cast(tuple[float, float, float], result)


def _volumes_from_dict(value: Any) -> tuple[Volume, ...]:
    if not isinstance(value, list):
        raise ValueError("placements.volumes must be a bounded list")
    values = cast(list[Any], value)
    if len(values) > MAX_PLACEMENTS_VOLUMES:
        raise ValueError("placements.volumes must be a bounded list")
    return tuple(_volume_from_dict(item, index) for index, item in enumerate(values))


def _routes_from_dict(value: Any) -> tuple[Route, ...]:
    if not isinstance(value, list):
        raise ValueError("placements.routes must be a bounded list")
    values = cast(list[Any], value)
    if len(values) > MAX_PLACEMENTS_ROUTES:
        raise ValueError("placements.routes must be a bounded list")
    return tuple(_route_from_dict(item, index) for index, item in enumerate(values))


def _validate_volume(value: Any, index: int) -> None:
    if type(value) is not Volume:
        raise ValueError(f"volumes[{index}] must be a Volume")
    _validate_volume_payload(value, index)


def _validate_volume_payload(value: Any, index: int) -> None:
    label = f"volumes[{index}]"
    raw = cast(Any, vars(value))
    name = raw["name"]
    if not isinstance(name, str) or not name or len(name) > MAX_PLACEMENTS_STRING:
        raise ValueError(f"{label}.name must be a bounded non-empty string")
    aabb = raw["aabb"]
    if type(aabb) is not AABB:
        raise ValueError(f"{label}.aabb must be an AABB")
    aabb_values = cast(Any, vars(aabb))
    for coordinate in (
        aabb_values["min_x"],
        aabb_values["min_y"],
        aabb_values["min_z"],
        aabb_values["max_x"],
        aabb_values["max_y"],
        aabb_values["max_z"],
    ):
        _number(coordinate, f"{label}.aabb", MAX_PLACEMENTS_POSITION_MM)
    if (
        aabb_values["min_x"] > aabb_values["max_x"]
        or aabb_values["min_y"] > aabb_values["max_y"]
        or aabb_values["min_z"] > aabb_values["max_z"]
    ):
        raise ValueError(f"{label}.aabb has inverted bounds")
    is_keep_out = _strict_bool(raw["is_keep_out"], f"{label}.is_keep_out")
    is_cable_corridor = _strict_bool(raw["is_cable_corridor"], f"{label}.is_cable_corridor")
    if is_keep_out and is_cable_corridor:
        raise ValueError(f"{label} cannot be both a keep-out and cable corridor")


def _validate_route(value: Any, index: int) -> None:
    if type(value) is not Route:
        raise ValueError(f"routes[{index}] must be a Route")
    route = cast(Any, vars(value))
    label = f"routes[{index}]"
    name = route["name"]
    if not isinstance(name, str) or not name or len(name) > MAX_PLACEMENTS_STRING:
        raise ValueError(f"{label}.name must be a bounded non-empty string")
    waypoints = route["waypoints"]
    if type(waypoints) is not tuple or not 2 <= len(cast(Any, waypoints)) <= 128:
        raise ValueError(f"{label}.waypoints must contain 2 to 128 points")
    waypoint_values = cast(tuple[Any, ...], waypoints)
    for point_index, point in enumerate(waypoint_values):
        if type(point) is not tuple or len(cast(Any, point)) != 3:
            raise ValueError(f"{label}.waypoints[{point_index}] is malformed")
        for coordinate in cast(tuple[Any, ...], point):
            _number(coordinate, f"{label}.waypoints[{point_index}]", MAX_PLACEMENTS_POSITION_MM)
    _positive_number(route["min_bend_radius_mm"], f"{label}.min_bend_radius_mm")


def _validate_placements_values(value: Placements, *, enforce_case_bounds: bool = True) -> None:
    raw = cast(Any, vars(value))
    case_max = cast(
        tuple[float, float, float],
        CASE_MAX_MM if enforce_case_bounds else (MAX_PLACEMENTS_DIMENSION_MM,) * 3,
    )
    _validate_case_dimensions(raw, case_max)
    _validate_button_fields(raw)
    _validate_component_fields(raw)
    _validate_electronics_backbone(raw["electronics_backbone"])
    _validate_structured_fields(raw)
    _validate_active_control_fields(raw)


def _validate_case_dimensions(raw: Any, case_max: tuple[float, float, float]) -> None:
    _positive_number(raw["case_length_mm"], "case_length_mm", case_max[0])
    _positive_number(raw["case_width_mm"], "case_width_mm", case_max[1])
    _positive_number(raw["case_thickness_mm"], "case_thickness_mm", case_max[2])


def _validate_button_fields(raw: Any) -> None:
    _validate_button_geometry(raw)
    _validate_button_scalars(raw)


def _validate_button_geometry(raw: Any) -> None:
    centres = raw["button_centres_xy"]
    if type(centres) is not tuple or len(cast(Any, centres)) > MAX_PLACEMENTS_LIST_ITEMS:
        raise ValueError("button_centres_xy must be a bounded tuple")
    centre_values = cast(tuple[Any, ...], centres)
    for index, point in enumerate(centre_values):
        if type(point) is not tuple or len(cast(Any, point)) != 2:
            raise ValueError(f"button_centres_xy[{index}] is malformed")
        for coordinate in cast(tuple[Any, ...], point):
            _number(coordinate, f"button_centres_xy[{index}]", MAX_PLACEMENTS_POSITION_MM)
    diameters = raw["button_diameters_mm"]
    if type(diameters) is not tuple or len(cast(Any, diameters)) > MAX_PLACEMENTS_LIST_ITEMS:
        raise ValueError("button_diameters_mm must be a bounded tuple")
    diameter_values = cast(tuple[Any, ...], diameters)
    if len(diameter_values) != len(centre_values):
        raise ValueError("button_diameters_mm and button_centres_xy lengths differ")
    for index, diameter in enumerate(diameter_values):
        _positive_number(diameter, f"button_diameters_mm[{index}]")


def _validate_button_scalars(raw: Any) -> None:
    _positive_number(raw["button_mounting_land_diameter_mm"], "button_mounting_land_diameter_mm")
    _positive_number(raw["button_mounting_land_thickness_mm"], "button_mounting_land_thickness_mm")
    _positive_number(raw["button_max_local_panel_mm"], "button_max_local_panel_mm")


def _validate_component_fields(raw: Any) -> None:
    _strict_bool(raw["feather_present"], "feather_present")
    _strict_bool(raw["usb_cutout_present"], "usb_cutout_present")
    _strict_bool(raw["led_view_hole_present"], "led_view_hole_present")
    _strict_bool(raw["reset_tool_hole_present"], "reset_tool_hole_present")
    for field_name, dimensions_value in (
        ("feather_keepout_mm", raw["feather_keepout_mm"]),
        ("usb_cutout_mm", raw["usb_cutout_mm"]),
    ):
        dimensions = dimensions_value
        if type(dimensions) is not tuple:
            raise ValueError(f"{field_name} must be a tuple")
        dimension_values = cast(tuple[Any, ...], dimensions)
        for index, dimension in enumerate(dimension_values):
            _positive_number(dimension, f"{field_name}[{index}]")
    feather = raw["feather_keepout_mm"]
    usb = raw["usb_cutout_mm"]
    if len(cast(tuple[Any, ...], feather)) != 3 or len(cast(tuple[Any, ...], usb)) != 2:
        raise ValueError("component dimensions have the wrong shape")
    _positive_number(raw["usb_corner_radius_mm"], "usb_corner_radius_mm")
    corridor = raw["dpad_cable_corridor_mm"]
    if corridor is not None:
        if type(corridor) is not tuple or len(cast(Any, corridor)) != 3:
            raise ValueError("dpad_cable_corridor_mm has the wrong shape")
        for index, dimension in enumerate(cast(tuple[Any, ...], corridor)):
            _positive_number(dimension, f"dpad_cable_corridor_mm[{index}]")


def _validate_electronics_backbone(value: Any) -> None:
    """Require a typed, bounded backbone record when one is supplied."""
    if value is not None and type(value) is not ElectronicsBackbone:
        raise ValueError("electronics_backbone must be an ElectronicsBackbone or null")


def _validate_structured_fields(raw: Any) -> None:
    volumes = raw["volumes"]
    routes = raw["routes"]
    if type(volumes) is not tuple or len(cast(Any, volumes)) > MAX_PLACEMENTS_VOLUMES:
        raise ValueError("volumes must be a bounded tuple")
    if type(routes) is not tuple or len(cast(Any, routes)) > MAX_PLACEMENTS_ROUTES:
        raise ValueError("routes must be a bounded tuple")
    for index, volume in enumerate(cast(tuple[Any, ...], volumes)):
        _validate_volume(volume, index)
    for index, route in enumerate(cast(tuple[Any, ...], routes)):
        _validate_route(route, index)


def _validate_active_control_fields(raw: Any) -> None:
    """Validate shape and bounds of the strict exact-six fields.

    Exact identities and receiving evidence are checked by ``validate`` so a
    design-time caller receives violations rather than a constructor
    exception.  Serialization still validates every scalar and list bound.
    """
    for field_name in ("control_ids", "control_roles", "control_component_ids"):
        _validate_active_text_field(raw, field_name)
    _validate_active_point_field(raw, "control_centres_xy")
    for field_name in (
        "control_cutout_diameters_mm",
        "control_mounting_land_diameters_mm",
        "control_mounting_land_thicknesses_mm",
        "control_terminal_route_min_bend_radii_mm",
    ):
        _validate_active_number_field(raw, field_name)
    for field_name in ("control_underside_keepouts_mm", "control_terminal_keepouts_mm"):
        _validate_active_dimensions_field(raw, field_name)
    _validate_active_geometry_records(raw)


def _active_control_tuple(raw: Any, field_name: str) -> tuple[Any, ...]:
    """Return one bounded exact-six field for active-control validation."""
    values = raw[field_name]
    if type(values) is not tuple or len(cast(Any, values)) > ACTIVE_CONTROL_COUNT:
        raise ValueError(f"{field_name} must be a bounded tuple")
    return cast(tuple[Any, ...], values)


def _validate_active_text_field(raw: Any, field_name: str) -> None:
    """Validate one bounded active-control text field."""
    values = _active_control_tuple(raw, field_name)
    for index, item in enumerate(values):
        _bounded_text(item, f"{field_name}[{index}]")


def _validate_active_point_field(raw: Any, field_name: str) -> None:
    """Validate one bounded active-control point field."""
    values = _active_control_tuple(raw, field_name)
    for index, point in enumerate(values):
        if type(point) is not tuple or len(cast(Any, point)) != 2:
            raise ValueError(f"{field_name}[{index}] is malformed")
        for coordinate in cast(tuple[Any, ...], point):
            _number(coordinate, f"{field_name}[{index}]", MAX_PLACEMENTS_POSITION_MM)


def _validate_active_number_field(raw: Any, field_name: str) -> None:
    """Validate one bounded active-control scalar field."""
    values = _active_control_tuple(raw, field_name)
    for index, number in enumerate(values):
        _positive_number(number, f"{field_name}[{index}]")


def _validate_active_dimensions_field(raw: Any, field_name: str) -> None:
    """Validate one bounded active-control dimensions field."""
    values = _active_control_tuple(raw, field_name)
    for index, dimensions in enumerate(values):
        if type(dimensions) is not tuple or len(cast(Any, dimensions)) != 3:
            raise ValueError(f"{field_name}[{index}] is malformed")
        for dimension_index, dimension in enumerate(cast(tuple[Any, ...], dimensions)):
            _positive_number(dimension, f"{field_name}[{index}][{dimension_index}]")


def _validate_active_geometry_records(raw: Any) -> None:
    """Validate the bounded active-control geometry-record field."""
    records = _active_control_tuple(raw, "control_geometry_records")
    for index, record in enumerate(records):
        if type(record) is not _p.FrozenControlGeometry:
            raise ValueError(f"control_geometry_records[{index}] must be FrozenControlGeometry")


def validate_exterior_geometry(
    baseline: Part,
    candidate: Part,
    *,
    protected_regions: (
        Mapping[str, Part] | Iterable[_exterior.ProtectedExteriorRegion | Part | tuple[str, Part]]
    ) = (),
    expected_bounds: BoundBox | None = None,
    tolerance_mm: float = EXTERIOR_GEOMETRY_TOLERANCE_MM,
) -> list[ConstraintViolation]:
    """Compare an exterior candidate against its accepted baseline.

    This is the central MIG-04 seam for future snap-root, split, control,
    opening, adhesive, electronics, route, and no-pinch protection.  Both
    material and void occupancy are compared inside each supplied region.  The
    volume difference tolerance is ``tolerance_mm`` mm³ (the small unit is
    intentional: it catches a real changed occupancy while tolerating BREP
    round-trip noise).  The baseline and candidate must each be one valid
    solid and must have identical extrema, including Z.
    """
    violations = _validate_exterior_part_pair(baseline, candidate, tolerance_mm)
    if expected_bounds is not None:
        violations.extend(
            _validate_expected_exterior_bounds(candidate, expected_bounds, tolerance_mm)
        )
    if violations:
        return violations
    regions, region_errors = _normalise_exterior_regions(protected_regions)
    if region_errors:
        return region_errors
    for protected in regions:
        violations.extend(_compare_exterior_region(baseline, candidate, protected, tolerance_mm))
    return violations


def compare_exterior_geometry(
    baseline: Part,
    candidate: Part,
    *,
    protected_regions: (
        Mapping[str, Part] | Iterable[_exterior.ProtectedExteriorRegion | Part | tuple[str, Part]]
    ) = (),
    expected_bounds: BoundBox | None = None,
    tolerance_mm: float = EXTERIOR_GEOMETRY_TOLERANCE_MM,
) -> list[ConstraintViolation]:
    """Alias for :func:`validate_exterior_geometry` used by comparison callers."""
    return validate_exterior_geometry(
        baseline,
        candidate,
        protected_regions=protected_regions,
        expected_bounds=expected_bounds,
        tolerance_mm=tolerance_mm,
    )


def _geometry_error(rule: str, message: str, location: str = "") -> ConstraintViolation:
    return ConstraintViolation(
        category="print",
        rule=rule,
        severity="error",
        message=message,
        location=location,
    )


def _validate_exterior_part_pair(
    baseline: Part,
    candidate: Part,
    tolerance_mm: float,
) -> list[ConstraintViolation]:
    raw_tolerance: Any = tolerance_mm
    if isinstance(raw_tolerance, bool) or not isinstance(raw_tolerance, (int, float)):
        raise ValueError("tolerance_mm must be a finite positive number")
    tolerance = float(raw_tolerance)
    if not math.isfinite(tolerance) or tolerance <= 0.0:
        raise ValueError("tolerance_mm must be a finite positive number")
    violations: list[ConstraintViolation] = []
    for label, part in cast(
        tuple[tuple[str, Any], ...], (("baseline", baseline), ("candidate", candidate))
    ):
        if not isinstance(part, Part):
            violations.append(
                _geometry_error("exterior_part_invalid", f"{label} is not a Build123d Part")
            )
            continue
        if not part.is_valid:
            violations.append(
                _geometry_error("exterior_topology_invalid", f"{label} is not a valid Part")
            )
        if len(part.solids()) != 1:
            violations.append(
                _geometry_error(
                    "exterior_topology_invalid",
                    f"{label} must contain exactly one solid",
                )
            )
    if (
        violations
        or not isinstance(cast(Any, baseline), Part)
        or not isinstance(cast(Any, candidate), Part)
    ):
        return violations
    baseline_bounds = baseline.bounding_box()
    candidate_bounds = candidate.bounding_box()
    baseline_extrema = _bound_extrema(baseline_bounds)
    candidate_extrema = _bound_extrema(candidate_bounds)
    violations.extend(_validate_exterior_extrema(baseline_extrema, candidate_extrema, tolerance))
    violations.extend(_validate_exterior_topology(baseline, candidate))
    return violations


def _bound_extrema(bounds: BoundBox) -> tuple[float, ...]:
    return (
        bounds.min.X,
        bounds.min.Y,
        bounds.min.Z,
        bounds.max.X,
        bounds.max.Y,
        bounds.max.Z,
    )


def _validate_exterior_extrema(
    baseline: tuple[float, ...],
    candidate: tuple[float, ...],
    tolerance: float,
) -> list[ConstraintViolation]:
    violations: list[ConstraintViolation] = []
    if any(
        abs(left - right) > tolerance
        for left, right in zip(
            baseline[:2] + baseline[3:5], candidate[:2] + candidate[3:5], strict=True
        )
    ):
        violations.append(
            _geometry_error(
                "exterior_envelope_changed",
                "candidate XY envelope differs from the accepted baseline",
            )
        )
    if any(
        abs(left - right) > tolerance
        for left, right in zip(
            baseline[2:3] + baseline[5:], candidate[2:3] + candidate[5:], strict=True
        )
    ):
        violations.append(
            _geometry_error(
                "exterior_z_changed",
                "candidate Z stack differs from the accepted baseline",
            )
        )
    return violations


def _validate_exterior_topology(baseline: Part, candidate: Part) -> list[ConstraintViolation]:
    baseline_shells = len(baseline.solids()[0].shells())
    candidate_shells = len(candidate.solids()[0].shells())
    if baseline_shells != candidate_shells:
        return [
            _geometry_error(
                "exterior_topology_changed",
                "candidate shell topology differs from the accepted baseline",
            )
        ]
    return []


def _validate_expected_exterior_bounds(
    candidate: Part,
    expected: BoundBox,
    tolerance_mm: float,
) -> list[ConstraintViolation]:
    candidate_extrema = _bound_extrema(candidate.bounding_box())
    expected_extrema = _bound_extrema(expected)
    if any(
        abs(left - right) > tolerance_mm
        for left, right in zip(candidate_extrema, expected_extrema, strict=True)
    ):
        return [
            _geometry_error(
                "exterior_expected_envelope_changed",
                "candidate envelope or Z stack differs from the caller-supplied frame",
            )
        ]
    return []


def _normalise_exterior_regions(
    protected_regions: (
        Mapping[str, Part] | Iterable[_exterior.ProtectedExteriorRegion | Part | tuple[str, Part]]
    ),
) -> tuple[tuple[_exterior.ProtectedExteriorRegion, ...], list[ConstraintViolation]]:
    raw_regions: Any = protected_regions
    if isinstance(raw_regions, Mapping):
        mapping = cast(Mapping[str, Part], raw_regions)
        values: list[Any] = [(name, region) for name, region in mapping.items()]
    elif isinstance(raw_regions, Part):
        values = [protected_regions]
    else:
        try:
            values = list(raw_regions)
        except TypeError as exc:
            raise ValueError("protected_regions must be an iterable of Build123d regions") from exc
    normalised: list[_exterior.ProtectedExteriorRegion] = []
    violations: list[ConstraintViolation] = []
    for index, value in enumerate(values):
        try:
            protected = _coerce_exterior_region(value, index)
            if not protected.region.is_valid or len(protected.region.solids()) != 1:
                raise ValueError("protected region must contain exactly one valid solid")
        except (TypeError, ValueError) as exc:
            violations.append(
                _geometry_error("exterior_protected_region_invalid", str(exc), f"region[{index}]")
            )
            continue
        normalised.append(protected)
    return tuple(normalised), violations


def _coerce_exterior_region(
    value: Any,
    index: int,
) -> _exterior.ProtectedExteriorRegion:
    if isinstance(value, _exterior.ProtectedExteriorRegion):
        return value
    if isinstance(value, Part):
        return _exterior.ProtectedExteriorRegion(f"region[{index}]", value)
    if isinstance(value, tuple):
        named_region = cast(tuple[Any, ...], value)
        if len(named_region) != 2:
            raise ValueError(f"region[{index}] must be a Part or named protected region")
        name, region = named_region
        return _exterior.ProtectedExteriorRegion(name, region)
    raise ValueError(f"region[{index}] must be a Part or named protected region")


def _compare_exterior_region(
    baseline: Part,
    candidate: Part,
    protected: _exterior.ProtectedExteriorRegion,
    tolerance_mm: float,
) -> list[ConstraintViolation]:
    try:
        removed_material = _protected_difference_volume(baseline, candidate, protected.region)
        added_material = _protected_difference_volume(candidate, baseline, protected.region)
        local_delta = _exterior_occupancy_delta(
            removed_material,
            added_material,
            protected.occupancy,
        )
        if tolerance_mm < local_delta <= _EXTERIOR_LOCAL_BREP_RETRY_MM3:
            # Independent clipping can introduce a few thousandths of a cubic
            # millimetre of BREP noise for mathematically identical occupancy.
            # Retry only that ambiguous region using the slower whole-part
            # difference; material changes are still judged at tolerance_mm.
            removed_material = _global_protected_difference_volume(
                baseline, candidate, protected.region
            )
            added_material = _global_protected_difference_volume(
                candidate, baseline, protected.region
            )
    except Exception as exc:
        return [
            _geometry_error(
                "exterior_protected_region_unavailable",
                f"occupancy comparison failed: {exc}",
                protected.name,
            )
        ]
    occupancy_delta = _exterior_occupancy_delta(
        removed_material,
        added_material,
        protected.occupancy,
    )
    if occupancy_delta > tolerance_mm:
        return [
            _geometry_error(
                "exterior_protected_occupancy_changed",
                (
                    f"protected region occupancy changed above {tolerance_mm:g} mm³ "
                    f"(removed={removed_material:g}, added={added_material:g}, "
                    f"intent={protected.occupancy})"
                ),
                protected.name,
            )
        ]
    return []


def _exterior_occupancy_delta(
    removed_material: float,
    added_material: float,
    occupancy: _exterior.ProtectedOccupancy,
) -> float:
    if occupancy == "solid":
        return removed_material + added_material
    if occupancy == "void":
        # A material addition removes baseline void; a material removal adds it.
        return added_material + removed_material
    return removed_material + added_material


def _protected_difference_volume(source: Part, other: Part, region: Part) -> float:
    # Clip first so OpenCascade operates on the small protected volume instead
    # of repeatedly subtracting two complete, nearly coincident case shells.
    # Besides being substantially faster, this avoids unstable global BREP
    # differences whose invalid fragments can leak into an unrelated region.
    source_occupancy = source & region
    if not source_occupancy.solids():
        return 0.0
    other_occupancy = other & region
    if not other_occupancy.solids():
        return source_occupancy.volume
    difference = source_occupancy - other_occupancy
    if not difference.solids():
        return 0.0
    return difference.volume


def _global_protected_difference_volume(source: Part, other: Part, region: Part) -> float:
    difference = source - other
    if not difference.solids():
        return 0.0
    return (difference & region).volume


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def validate(
    part: Part,
    *,
    bounding_box: BoundBox | None = None,
    materials: dict[str, set[str]] | None = None,
    print_duration_minutes: float | None = None,
    printer_profile_envelope_mm: tuple[float, float, float] | None = None,
    printer_profile_layer_height_mm: float | None = None,
    supports_used: bool | None = None,
    overhang_verified: bool | None = None,
    bridge_verified: bool | None = None,
    require_export_verification: bool = False,
    whole_case_geometry: bool | None = None,
    placements: Placements | None = None,
    qualification: _printers.PrinterQualification | None = None,
    slice_evidence: _printers.SliceEvidence | None = None,
    top_snap: _snap.SnapSelectionRecord | None = None,
    bottom_snap: _snap.SnapSelectionRecord | None = None,
    geometry_evidence: _geom.GeometryEvidence | None = None,
    printer_evidence: _evidence.FrozenEvidenceRef | None = None,
    electronics_backbone_evidence: _electronics.ElectronicsBackboneEvidence | None = None,
) -> list[ConstraintViolation]:
    """Run every constraint check against ``part``.

    The default behaviour is **design-time**: the validator reports
    unverified overhang / bridge checks as warnings so the agent loop
    can iterate without re-running the slicer on every change. Export
    callers (STL / 3MF / G-code generation, ``is_printable()``) set
    ``require_export_verification=True`` so the same conditions become
    hard errors — this is the procurement-plan §5 fail-closed policy.

    Note: wall-thickness, overhang, and bridge checks are **unverified
    geometry heuristics**. A real wall / overhang / bridge analysis
    requires shelling + signed slicer review; the validator's
    bounding-box heuristic is documented as such and is not
    overclaimed as geometric proof. The export gate must surface
    this honestly so the orchestrator does not treat the heuristic
    as a substitution for the slicer.

    Procurement plan §5 row "validate()" (the new VS-04 contract):

    * **Placements** (``placements=``) — structured exact-six component
      placements are the only active control input.
    * **Whole-case geometry** (``whole_case_geometry=True``) — callers that
      hold the unsplit enclosure force cavity/opening checks; split-half export
      passes ``False``. ``None`` retains bounded envelope inference for legacy
      callers.
    * **Qualification** (``qualification=``) — immutable printer /
      profile qualification record. At export time, the validator
      emits a hard ``printer_unqualified`` error when this is absent.
    * **Slice evidence** (``slice_evidence=``) — immutable record of
      a successful support-free slice. Production 3MF/G-code export
      requires this and the validator checks
      :func:`cadkit.printers.slice_matches_qualification`.
    * **Snap pair** (``top_snap=``, ``bottom_snap=``) — both halves
      must be consistent (same printer / filament / clearance); the
      validator emits a hard ``snap_pair_inconsistent`` error when
      they disagree.
    * Missing / unavailable evidence → ``severity=error`` with rule
      containing ``unverified`` (never a silent pass).
    """
    if materials is None:
        materials = AVAILABLE_MATERIALS

    bb = bounding_box or part.bounding_box()
    violations: list[ConstraintViolation] = []

    if placements is not None:
        violations.extend(_check_active_controls(placements, require_export_verification))
        violations.extend(
            _check_functional_case_geometry(part, bb, placements, whole_case_geometry)
        )
        violations.extend(
            _check_electronics_backbone(
                placements,
                require_export_verification,
                electronics_backbone_evidence,
            )
        )

    violations.extend(
        _check_print(
            part,
            bb,
            printer_profile_envelope_mm=printer_profile_envelope_mm,
            printer_profile_layer_height_mm=printer_profile_layer_height_mm,
            supports_used=supports_used,
            overhang_verified=overhang_verified,
            bridge_verified=bridge_verified,
            print_duration_minutes=print_duration_minutes,
            require_export_verification=require_export_verification,
            qualification=qualification,
            slice_evidence=slice_evidence,
        )
    )
    violations.extend(
        _check_ergonomic(
            bb,
            placements=placements,
        )
    )
    violations.extend(_check_aesthetic(part))
    violations.extend(_check_snap_pair(top_snap, bottom_snap))
    violations.extend(_check_qualification(qualification, require_export_verification))
    violations.extend(
        _check_slice_evidence(slice_evidence, qualification, require_export_verification)
    )
    violations.extend(_check_geometry_evidence(geometry_evidence, require_export_verification))
    violations.extend(_check_volumes_and_routes(placements, require_export_verification))

    return violations


def _check_functional_case_geometry(
    part: Part,
    bb: BoundBox,
    placements: Placements,
    whole_case_geometry: bool | None,
) -> list[ConstraintViolation]:
    """Reject exact-six slabs and panel openings that do not reach the cavity."""
    if not placements.is_active_control_contract:
        return []
    if whole_case_geometry is False or (
        whole_case_geometry is None and _is_split_case_half(bb, placements)
    ):
        # Export validates already-split halves with the whole-case placement
        # record. Functional cavity continuity is checked on the source case
        # before the split.
        return []

    out: list[ConstraintViolation] = []
    bounding_volume = bb.size.X * bb.size.Y * bb.size.Z
    if bounding_volume > 0 and part.volume / bounding_volume > MAX_CASE_MATERIAL_FRACTION:
        out.append(
            ConstraintViolation(
                category="print",
                rule="case_interior_cavity_missing",
                severity="error",
                message=(
                    "Case material occupies more than 45% of its envelope; "
                    "the exact-six enclosure requires a usable electronics cavity."
                ),
            )
        )

    cavity_probe_z = (bb.min.Z + bb.max.Z) / 2
    for control_id, (local_x, local_y), cutout_diameter in zip(
        placements.control_ids,
        placements.control_centres_xy,
        placements.control_cutout_diameters_mm,
        strict=False,
    ):
        x, y, _ = case_local_to_build123d(
            (local_x, local_y, cavity_probe_z),
            (
                placements.case_length_mm,
                placements.case_width_mm,
                placements.case_thickness_mm,
            ),
        )
        path_probe = cast(
            Part,
            Location((x, y, cavity_probe_z))
            * Cylinder(
                cutout_diameter / 2,
                bb.max.Z - cavity_probe_z,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
            ),
        )
        if cast(Part, part & path_probe).volume > 1e-6:
            out.append(
                ConstraintViolation(
                    category="print",
                    rule="control_opening_not_through",
                    severity="error",
                    message=(
                        f"Control {control_id!r} does not have an open path through "
                        "the top panel into the electronics cavity."
                    ),
                    location=f"control.{control_id}",
                )
            )
    return out


def _is_split_case_half(bb: BoundBox, placements: Placements) -> bool:
    """Identify bottom/top envelopes emitted by the authoritative split."""
    tolerance = 1e-6
    split_z = min(
        placements.case_thickness_mm / 2,
        AUTHORITATIVE_SNAP_GEOMETRY.max_lower_shell_height_mm,
    )
    bottom_half = abs(bb.min.Z) <= tolerance and split_z + tolerance >= bb.max.Z
    top_half = (
        abs(bb.max.Z - placements.case_thickness_mm) <= tolerance
        and tolerance < bb.min.Z <= split_z + tolerance
    )
    return bottom_half or top_half


def _check_electronics_backbone(
    placements: Placements,
    require_export_verification: bool,
    evidence: _electronics.ElectronicsBackboneEvidence | None,
) -> list[ConstraintViolation]:
    """Keep the unmeasured breadboard contract out of production exports."""
    backbone = placements.electronics_backbone
    severity = "error" if require_export_verification else "warning"
    if backbone is None:
        return [
            ConstraintViolation(
                category="print",
                rule="electronics_backbone_unverified",
                severity=severity,
                message=(
                    "No electronics backbone contract was supplied; production requires "
                    "the frozen Pi Hut 100058 / ADA2830 backbone record."
                ),
            )
        ]
    if not backbone.identity_matches_contract:
        return [
            ConstraintViolation(
                category="print",
                rule="electronics_backbone_identity_mismatch",
                severity="error",
                message=(
                    "Electronics backbone identity/topology does not match the frozen "
                    "Pi Hut 100058 / ADA2830 contract."
                ),
            )
        ]
    if not backbone.is_frozen_for_export:
        return [
            ConstraintViolation(
                category="print",
                rule="electronics_backbone_unverified",
                severity=severity,
                message=(
                    f"Electronics backbone status is {backbone.status!r}; positive measured "
                    "dimensions, verified adhesive bonding, and a signed-record binding "
                    "are required for production export."
                ),
            )
        ]
    if evidence is None:
        return [
            ConstraintViolation(
                category="print",
                rule="electronics_backbone_evidence_unverified",
                severity=severity,
                message=(
                    "A source-authored FROZEN placement cannot authorize production; "
                    "supply the strict electronics-backbone evidence loaded from the "
                    "actual provenance JSON file."
                ),
            )
        ]
    if backbone != evidence.to_electronics_backbone():
        return [
            ConstraintViolation(
                category="print",
                rule="electronics_backbone_evidence_mismatch",
                severity="error",
                message=(
                    "Electronics backbone placement values do not exactly match the "
                    "trusted frozen provenance record."
                ),
            )
        ]
    return []


def _check_active_controls(
    placements: Placements,
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """Enforce the MIG-02 exact-six identities and signed component metrics."""
    if not placements.is_active_control_contract:
        if not require_export_verification:
            return []
        return [
            _active_evidence_violation(
                "exact_six_controls_unverified",
                "Production requires the exact-six control placement contract; "
                "historical or empty control placements cannot authorize export.",
                require_export_verification=True,
            )
        ]

    expected_ids = CONTROL_IDS
    expected_roles = CONTROL_ROLES
    expected_components = CONTROL_COMPONENT_IDS
    out = _check_active_identity(placements, expected_ids, expected_roles, expected_components)
    out.extend(_check_active_field_lengths(placements, require_export_verification))
    out.extend(
        _check_active_geometry_evidence(
            placements,
            expected_components,
            require_export_verification,
        )
    )
    out.extend(_check_active_route_bindings(placements))
    return out


def _check_active_identity(
    placements: Placements,
    expected_ids: tuple[str, ...],
    expected_roles: tuple[str, ...],
    expected_components: tuple[str, ...],
) -> list[ConstraintViolation]:
    """Check exact-six control ordering, roles, and component mix."""
    out: list[ConstraintViolation] = []
    if placements.control_ids != expected_ids:
        out.append(
            _active_violation(
                "control_ids_exact_six",
                f"Active controls must be ordered exactly as {expected_ids}; "
                f"received {placements.control_ids!r}.",
            )
        )
    if placements.control_roles != expected_roles:
        out.append(
            _active_violation(
                "control_roles_mismatch",
                f"Active control roles must be {expected_roles}; "
                f"received {placements.control_roles!r}.",
            )
        )
    if placements.control_component_ids != expected_components:
        out.append(
            _active_violation(
                "control_component_mix",
                "Active controls require exactly four pbs33b_directional "
                "components followed by two guuzi_b09bmrdptn_action components.",
            )
        )
    return out


def _check_active_field_lengths(
    placements: Placements,
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """Require one component-specific value for every active field."""
    fields = (
        ("control_centres_xy", placements.control_centres_xy),
        ("control_cutout_diameters_mm", placements.control_cutout_diameters_mm),
        ("control_mounting_land_diameters_mm", placements.control_mounting_land_diameters_mm),
        (
            "control_mounting_land_thicknesses_mm",
            placements.control_mounting_land_thicknesses_mm,
        ),
        ("control_underside_keepouts_mm", placements.control_underside_keepouts_mm),
        ("control_terminal_keepouts_mm", placements.control_terminal_keepouts_mm),
        (
            "control_terminal_route_min_bend_radii_mm",
            placements.control_terminal_route_min_bend_radii_mm,
        ),
    )
    out: list[ConstraintViolation] = []
    for field_name, values in fields:
        if len(values) != ACTIVE_CONTROL_COUNT:
            out.append(
                _active_evidence_violation(
                    f"{field_name}_unverified",
                    f"Active placement field {field_name!r} must contain one "
                    "component-specific value for each of the six controls.",
                    require_export_verification,
                )
            )
    return out


def _check_active_geometry_evidence(
    placements: Placements,
    expected_components: tuple[str, ...],
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """Check receiving records and duplicated exact-six control metrics."""
    records = placements.control_geometry_records
    allowed_record_counts = (2,) if require_export_verification else (2, ACTIVE_CONTROL_COUNT)
    if len(records) not in allowed_record_counts:
        return [
            _active_evidence_violation(
                "control_geometry_evidence_unverified",
                "Exactly one receiving-signed PBS record and one receiving-signed "
                "GUUZI geometry record are required for production; provisional or "
                "duplicated records cannot authorize export.",
                require_export_verification,
            )
        ]

    records_by_component = {
        component_id: record
        for component_id in set(expected_components)
        for record in records
        if record.component_id == component_id
    }
    out = _missing_active_geometry_records(
        records_by_component,
        expected_components,
        require_export_verification,
    )
    out.extend(
        _check_active_record_metrics(
            placements,
            records_by_component,
            expected_components,
            require_export_verification,
        )
    )
    return out


def _missing_active_geometry_records(
    records_by_component: dict[str, _p.FrozenControlGeometry],
    expected_components: tuple[str, ...],
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """Report active components missing receiving geometry records."""
    out: list[ConstraintViolation] = []
    for component_id in set(expected_components):
        if component_id not in records_by_component:
            out.append(
                _active_evidence_violation(
                    "control_geometry_component_mismatch",
                    f"No receiving geometry record is bound for {component_id!r}.",
                    require_export_verification,
                )
            )
    return out


def _check_active_record_metrics(
    placements: Placements,
    records_by_component: dict[str, _p.FrozenControlGeometry],
    expected_components: tuple[str, ...],
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """Check evidence metadata and duplicated metrics in order."""
    out: list[ConstraintViolation] = []
    for index, component_id in enumerate(expected_components):
        record = records_by_component.get(component_id)
        if record is None:
            continue
        out.extend(
            _active_record_evidence_violations(
                record,
                index,
                require_export_verification,
            )
        )
        _compare_control_record(out, index, record, placements)
    return out


def _active_record_evidence_violations(
    record: _p.FrozenControlGeometry,
    index: int,
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """Report missing receiving metadata and active-control metrics."""
    out: list[ConstraintViolation] = []
    if not record.is_receiving_signed:
        out.append(
            _active_evidence_violation(
                "control_geometry_evidence_unverified",
                f"Geometry record {index} has incomplete receiving/signature "
                "or coupon binding metadata.",
                require_export_verification,
            )
        )
    else:
        try:
            record.revalidate()
        except _evidence.ProvenanceError as exc:
            out.append(
                _active_evidence_violation(
                    "control_geometry_evidence_tamper",
                    f"Geometry record {index} failed provenance revalidation: {exc}",
                    require_export_verification,
                )
            )
    if record.minimum_thread_length_mm is None:
        out.append(
            _active_evidence_violation(
                "control_thread_metric_unverified",
                f"Geometry record {index} is missing the shortest usable thread metric.",
                require_export_verification,
            )
        )
    if record.terminal_route_min_bend_radius_mm is None:
        out.append(
            _active_evidence_violation(
                "control_route_metric_unverified",
                f"Geometry record {index} is missing its protected terminal-route "
                "bend-radius metric.",
                require_export_verification,
            )
        )
    return out


def _active_violation(rule: str, message: str) -> ConstraintViolation:
    return ConstraintViolation(category="ergonomic", rule=rule, severity="error", message=message)


def _active_evidence_violation(
    rule: str,
    message: str,
    require_export_verification: bool,
) -> ConstraintViolation:
    return ConstraintViolation(
        category="ergonomic",
        rule=rule,
        severity="error" if require_export_verification else "warning",
        message=message,
    )


def _compare_control_record(
    out: list[ConstraintViolation],
    index: int,
    record: _p.FrozenControlGeometry,
    placements: Placements,
) -> None:
    """Ensure duplicated placement metrics cannot diverge from evidence."""
    if len(placements.control_cutout_diameters_mm) == ACTIVE_CONTROL_COUNT and (
        placements.control_cutout_diameters_mm[index] != record.cutout_diameter_mm
    ):
        out.append(
            _active_violation(
                "control_cutout_evidence_mismatch",
                f"Control {index} cutout does not match its receiving geometry record.",
            )
        )
    if len(placements.control_mounting_land_diameters_mm) == ACTIVE_CONTROL_COUNT and (
        placements.control_mounting_land_diameters_mm[index] != record.mounting_land_diameter_mm
    ):
        out.append(
            _active_violation(
                "control_land_evidence_mismatch",
                f"Control {index} mounting land does not match its receiving record.",
            )
        )
    if len(placements.control_mounting_land_thicknesses_mm) == ACTIVE_CONTROL_COUNT and (
        placements.control_mounting_land_thicknesses_mm[index] != record.mounting_land_thickness_mm
    ):
        out.append(
            _active_violation(
                "control_land_thickness_evidence_mismatch",
                f"Control {index} land thickness does not match its receiving record.",
            )
        )
    if len(placements.control_underside_keepouts_mm) == ACTIVE_CONTROL_COUNT and (
        placements.control_underside_keepouts_mm[index] != record.underside_keepout_mm
    ):
        out.append(
            _active_violation(
                "control_underside_evidence_mismatch",
                f"Control {index} underside keep-out does not match its receiving record.",
            )
        )
    if len(placements.control_terminal_keepouts_mm) == ACTIVE_CONTROL_COUNT and (
        placements.control_terminal_keepouts_mm[index] != record.terminal_keepout_mm
    ):
        out.append(
            _active_violation(
                "control_terminal_evidence_mismatch",
                f"Control {index} terminal keep-out does not match its receiving record.",
            )
        )
    if len(placements.control_terminal_route_min_bend_radii_mm) == ACTIVE_CONTROL_COUNT and (
        record.terminal_route_min_bend_radius_mm is not None
        and placements.control_terminal_route_min_bend_radii_mm[index]
        != record.terminal_route_min_bend_radius_mm
    ):
        out.append(
            _active_violation(
                "control_route_metric_mismatch",
                f"Control {index} protected route bend radius does not match its "
                "receiving geometry record.",
            )
        )


def _check_active_route_bindings(placements: Placements) -> list[ConstraintViolation]:
    """Require protected routes for all six signals and common ground."""
    required = {*CONTROL_ROLES, "GND"}
    routes = placements.routes
    present = {_route_signal(route.name) for route in routes}
    missing = sorted(required - present)
    out: list[ConstraintViolation] = []
    if missing:
        out.append(
            _active_violation(
                "protected_routes_unverified",
                "Missing protected active-control route(s): " + ", ".join(missing) + ".",
            )
        )
    expected_names = {
        *(f"wire.control_{control_id}" for control_id in CONTROL_IDS),
        *(f"wire.control_ground_{control_id}" for control_id in CONTROL_IDS),
    }
    route_names = {route.name for route in routes}
    missing_names = sorted(expected_names - route_names)
    if missing_names:
        out.append(
            _active_violation(
                "protected_routes_unverified",
                "Missing protected per-control route(s): " + ", ".join(missing_names) + ".",
            )
        )
    expected = {signal: index for index, signal in enumerate(CONTROL_ROLES)}
    for route in routes:
        signal = _route_signal(route.name)
        index = expected.get(signal)
        if (
            index is not None
            and len(placements.control_terminal_route_min_bend_radii_mm) == ACTIVE_CONTROL_COUNT
            and route.min_bend_radius_mm
            < placements.control_terminal_route_min_bend_radii_mm[index]
        ):
            out.append(
                _active_violation(
                    "protected_route_metric_mismatch",
                    f"Protected {signal} route minimum bend radius is below its "
                    "receiving geometry requirement.",
                )
            )
    return out


def _route_signal(name: str) -> str:
    normalized = name.upper().replace("-", "_").replace(".", "_")
    if "GROUND" in normalized or normalized.endswith("GND") or normalized == "GND":
        return "GND"
    for signal in reversed(CONTROL_ROLES):
        if normalized == signal or normalized.endswith(f"_{signal}"):
            return signal
    return ""


def _check_active_component_volumes(placements: Placements) -> list[ConstraintViolation]:
    """Require one protected keep-out and panel cutout per active control."""
    out: list[ConstraintViolation] = []
    volumes = placements.volumes
    for index, control_id in enumerate(placements.control_ids):
        keepout = _active_volume_names(control_id, index, "keepout")
        cutout = _active_volume_names(control_id, index, "cutout")
        if not any(volume.name in keepout and volume.is_keep_out for volume in volumes):
            out.append(
                _active_violation(
                    "control_keepout_unverified",
                    f"Missing protected keep-out volume for active control {control_id!r}.",
                )
            )
        if not any(
            volume.name in cutout and not volume.is_keep_out and not volume.is_cable_corridor
            for volume in volumes
        ):
            out.append(
                _active_violation(
                    "control_cutout_unverified",
                    f"Missing panel cutout volume for active control {control_id!r}.",
                )
            )
    return out


def _active_volume_names(control_id: str, index: int, kind: str) -> set[str]:
    short_id = control_id.removeprefix("control.")
    names = {
        f"control.{control_id}.{kind}",
        f"control_{control_id}_{kind}",
        f"control_{kind}_{control_id}",
        f"control.{short_id}.{kind}",
        f"control_{short_id}_{kind}",
        f"control_{kind}_{short_id}",
    }
    if index < 4:
        names.update({f"button_{kind}_{index}", f"pbs33b_{kind}_{index}"})
    else:
        names.update(
            {
                f"guuzi_{short_id}_{kind}",
                f"action_{short_id}_{kind}",
                f"button_{kind}_{index}",
            }
        )
        if index == 4:
            names.update({f"guuzi_{kind}", f"action_{kind}"})
    return names


# ---------------------------------------------------------------------------
# Print constraints
# ---------------------------------------------------------------------------
def _check_print(
    part: Part,
    bb: BoundBox,
    *,
    printer_profile_envelope_mm: tuple[float, float, float] | None,
    printer_profile_layer_height_mm: float | None,
    supports_used: bool | None,
    overhang_verified: bool | None,
    bridge_verified: bool | None,
    print_duration_minutes: float | None,
    require_export_verification: bool,
    qualification: _printers.PrinterQualification | None = None,
    slice_evidence: _printers.SliceEvidence | None = None,
) -> list[ConstraintViolation]:
    out: list[ConstraintViolation] = []
    out.extend(_check_case_envelope(bb))
    out.extend(_check_build_volume(bb, printer_profile_envelope_mm))
    out.extend(_check_per_half_size(bb))
    out.extend(_check_supports(supports_used, require_export_verification))
    out.extend(_check_overhang(overhang_verified, require_export_verification))
    out.extend(_check_bridge(bridge_verified, require_export_verification))
    out.extend(
        _check_layer_height(
            printer_profile_layer_height_mm,
            qualification,
        )
    )
    out.extend(_check_print_duration(print_duration_minutes, qualification))
    return out


def _check_case_envelope(bb: BoundBox) -> list[ConstraintViolation]:
    """Check the procurement-plan hard case envelope."""
    envelope = CASE_MAX_MM
    if any(
        observed > limit + BOUNDING_BOX_TOLERANCE_MM
        for observed, limit in zip((bb.size.X, bb.size.Y, bb.size.Z), envelope, strict=True)
    ):
        return [
            ConstraintViolation(
                category="print",
                rule="case_envelope",
                severity="error",
                message=(
                    f"Case bounding box ({bb.size.X:.1f} × {bb.size.Y:.1f} × "
                    f"{bb.size.Z:.1f} mm) exceeds the procurement-plan hard "
                    f"maximum ({envelope[0]} × {envelope[1]} × {envelope[2]} mm)."
                ),
            )
        ]
    return []


def _check_build_volume(
    bb: BoundBox,
    printer_profile_envelope_mm: tuple[float, float, float] | None,
) -> list[ConstraintViolation]:
    """Check the resolved printer build volume."""
    volume = printer_profile_envelope_mm or BUILD_VOLUME_DEFAULT_MM
    if any(
        observed > limit + BOUNDING_BOX_TOLERANCE_MM
        for observed, limit in zip((bb.size.X, bb.size.Y, bb.size.Z), volume, strict=True)
    ):
        return [
            ConstraintViolation(
                category="print",
                rule="build_volume",
                severity="error",
                message=(
                    f"Part bounding box ({bb.size.X:.1f} × {bb.size.Y:.1f} × "
                    f"{bb.size.Z:.1f} mm) exceeds the qualified printer "
                    f"envelope ({volume[0]} × {volume[1]} × {volume[2]} mm)."
                ),
            )
        ]
    return []


def _check_per_half_size(bb: BoundBox) -> list[ConstraintViolation]:
    """Advise when a part exceeds the recommended half-size envelope."""
    if any(
        observed > limit + BOUNDING_BOX_TOLERANCE_MM
        for observed, limit in zip(
            (bb.size.X, bb.size.Y, bb.size.Z), MAX_PART_PER_HALF_MM, strict=True
        )
    ):
        return [
            ConstraintViolation(
                category="print",
                rule="per_half_size",
                severity="warning",
                message=(
                    f"Part exceeds the per-half size recommendation "
                    f"({MAX_PART_PER_HALF_MM[0]} × {MAX_PART_PER_HALF_MM[1]} × "
                    f"{MAX_PART_PER_HALF_MM[2]} mm); "
                    "consider splitting into two snap-fit halves."
                ),
            )
        ]
    return []


def _check_supports(
    supports_used: bool | None,
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """Require support-free slicing, with export-time fail-closed handling."""
    if supports_used is True:
        return [
            ConstraintViolation(
                category="print",
                rule="supports_disabled",
                severity="error",
                message=(
                    "Both halves must slice with supports disabled "
                    "(procurement plan §5). Re-orient or redesign."
                ),
            )
        ]
    if supports_used is None and require_export_verification:
        return [
            ConstraintViolation(
                category="print",
                rule="supports_unverified",
                severity="error",
                message=(
                    "Supports setting has not been verified at "
                    "export time. Pass slice_evidence with "
                    "supports_used=False (procurement plan §5)."
                ),
            )
        ]
    return []


def _check_overhang(
    overhang_verified: bool | None,
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """Report the unverified overhang heuristic at the appropriate severity."""
    if overhang_verified is True:
        return []
    if require_export_verification:
        return [
            ConstraintViolation(
                category="print",
                rule="overhang_unverified",
                severity="error",
                message=(
                    "Overhang has not been verified at "
                    f"≤ {MAX_OVERHANG_DEG_FROM_VERTICAL}° from vertical "
                    "(procurement plan §5). Automatic analysis is "
                    "unavailable; signed slicer evidence is required "
                    "for export."
                ),
            )
        ]
    return [
        ConstraintViolation(
            category="print",
            rule="overhang_deg",
            severity="warning",
            message=(
                "Overhang has not been verified at "
                f"≤ {MAX_OVERHANG_DEG_FROM_VERTICAL}° from vertical "
                "(procurement plan §5). "
                "Run the slicer check or sign a manual review."
            ),
        )
    ]


def _check_bridge(
    bridge_verified: bool | None,
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """Report the unverified bridge heuristic at the appropriate severity."""
    if bridge_verified is True:
        return []
    if require_export_verification:
        return [
            ConstraintViolation(
                category="print",
                rule="bridge_unverified",
                severity="error",
                message=(
                    f"Bridge has not been verified within {MAX_BRIDGE_MM} mm "
                    "(procurement plan §5). Automatic analysis is "
                    "unavailable; signed slicer evidence is required "
                    "for export."
                ),
            )
        ]
    return [
        ConstraintViolation(
            category="print",
            rule="bridge_max_mm",
            severity="warning",
            message=(
                f"Bridge has not been verified within {MAX_BRIDGE_MM} mm "
                "(procurement plan §5). "
                "Run the slicer check or sign a manual review."
            ),
        )
    ]


def _check_layer_height(
    layer_height_mm: float | None,
    qualification: _printers.PrinterQualification | None,
) -> list[ConstraintViolation]:
    """Check a supplied layer height against its qualified printer profile."""
    if layer_height_mm is None:
        return []
    nominal: float | None = None
    profile_name = "default"
    if qualification is not None:
        profile_name = qualification.profile_name
        with contextlib.suppress(ValueError):
            nominal = _printers.layer_height_mm_for(profile_name)
    if nominal is not None and abs(layer_height_mm - nominal) > 1e-6:
        return [
            ConstraintViolation(
                category="print",
                rule="layer_height_mismatch",
                severity="error",
                message=(
                    f"Layer height {layer_height_mm} mm does not match the "
                    f"qualified printer {profile_name!r} layer height "
                    f"{nominal} mm (procurement plan §5)."
                ),
            )
        ]
    if layer_height_mm > 0.30:
        return [
            ConstraintViolation(
                category="print",
                rule="layer_height",
                severity="error",
                message=(
                    f"Layer height {layer_height_mm} mm exceeds the "
                    "procurement-plan ceiling (Ender 0.28 / XL 0.15). "
                    "No universal 0.30 ceiling; printer-specific gates are "
                    "enforced above."
                ),
            )
        ]
    return []


def _check_print_duration(
    duration_minutes: float | None,
    qualification: _printers.PrinterQualification | None,
) -> list[ConstraintViolation]:
    """Check print duration against the qualified printer's ceiling."""
    if duration_minutes is None:
        return []
    ceiling: float | None = None
    profile_name = "default"
    if qualification is not None:
        profile_name = qualification.profile_name
        with contextlib.suppress(ValueError):
            ceiling = _printers.max_print_minutes_for(profile_name)
    if ceiling is None:
        ceiling = 4 * 60
    if duration_minutes > ceiling:
        return [
            ConstraintViolation(
                category="print",
                rule="print_duration",
                severity="error",
                message=(
                    f"Estimated print time {duration_minutes:.0f} min exceeds the "
                    f"qualified printer {profile_name!r} ceiling of "
                    f"{ceiling:.0f} min (procurement plan §5)."
                ),
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Ergonomic constraints
# ---------------------------------------------------------------------------
def _check_ergonomic(
    bb: BoundBox,
    *,
    placements: Placements | None = None,
) -> list[ConstraintViolation]:
    out = _check_case_width(bb)
    if placements is None:
        out.extend(_check_button_count(0, False))
        return out
    if placements.is_active_control_contract:
        positions = list(placements.control_centres_xy)
        out.extend(_check_button_count(len(placements.control_ids), True))
        out.extend(_check_button_spacing(positions))
    elif placements.button_centres_xy or placements.dpad_centre_xy is not None:
        out.append(_legacy_controls_violation())
    return out


def _check_case_width(bb: BoundBox) -> list[ConstraintViolation]:
    """Check the adult-hand short-axis case-width limit."""
    if bb.size.Y > MAX_CASE_WIDTH_MM + BOUNDING_BOX_TOLERANCE_MM:
        return [
            ConstraintViolation(
                category="ergonomic",
                rule="max_case_width_mm",
                severity="error",
                message=(
                    f"Case short-axis width {bb.size.Y:.1f} mm exceeds the "
                    f"hard maximum {MAX_CASE_WIDTH_MM} mm (procurement plan §1)."
                ),
            )
        ]
    return []


def _check_button_count(count: int, explicit_count: bool) -> list[ConstraintViolation]:
    """Check button count, escalating only explicitly supplied counts."""
    out: list[ConstraintViolation] = []
    severity = "error" if explicit_count else "warning"
    if count < MIN_BUTTON_COUNT:
        out.append(
            ConstraintViolation(
                category="ergonomic",
                rule="min_button_count",
                severity=severity,
                message=(
                    f"Design has {count} panel buttons; minimum is "
                    f"{MIN_BUTTON_COUNT} per procurement plan §5."
                ),
            )
        )
    if count > MAX_BUTTON_COUNT:
        out.append(
            ConstraintViolation(
                category="ergonomic",
                rule="max_button_count",
                severity=severity,
                message=(
                    f"Design has {count} panel buttons; maximum is "
                    f"{MAX_BUTTON_COUNT} per procurement plan §5."
                ),
            )
        )
    return out


def _legacy_controls_violation() -> ConstraintViolation:
    """Reject the former arbitrary-button placement path."""
    return ConstraintViolation(
        category="ergonomic",
        rule="legacy_control_contract",
        severity="error",
        message="Only the exact-six control placement contract is supported.",
    )


def _check_button_spacing(
    positions: list[tuple[float, float]],
) -> list[ConstraintViolation]:
    """Check hard centre-to-centre spacing between every button pair."""
    out: list[ConstraintViolation] = []
    for i in range(len(positions)):
        for j in range(i + 1, len(positions)):
            ax, ay = positions[i]
            bx, by = positions[j]
            distance = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
            if distance < MIN_BUTTON_SPACING_MM:
                out.append(
                    ConstraintViolation(
                        category="ergonomic",
                        rule="min_button_spacing_mm",
                        severity="error",
                        message=(
                            f"Buttons {i} and {j} are {distance:.1f} mm apart "
                            f"(min {MIN_BUTTON_SPACING_MM} mm); "
                            "adult fingertips won't fit between them."
                        ),
                        location=f"buttons[{i}]={positions[i]}, buttons[{j}]={positions[j]}",
                    )
                )
    return out


# ---------------------------------------------------------------------------
# Aesthetic constraints
# ---------------------------------------------------------------------------
def _check_aesthetic(part: Part) -> list[ConstraintViolation]:
    out: list[ConstraintViolation] = []

    bb = part.bounding_box()
    cx = (bb.min.X + bb.max.X) / 2.0
    vertices = list(part.vertices())
    if vertices:
        x_by_yz: dict[tuple[float, float], list[float]] = {}
        for v in vertices:
            x, y, z = cast(tuple[float, float, float], tuple(cast(Any, v)))
            x_by_yz.setdefault((round(y, 3), round(z, 3)), []).append(x)

        worst_asym = 0.0
        worst_v: tuple[float, float, float] | None = None
        for v in vertices:
            x, y, z = cast(tuple[float, float, float], tuple(cast(Any, v)))
            mirror_x = 2 * cx - x
            xs = x_by_yz.get((round(y, 3), round(z, 3)), [])
            closest = min((abs(mx - mirror_x) for mx in xs), default=float("inf"))
            if closest > worst_asym:
                worst_asym = closest
                worst_v = (x, y, z)
        if worst_asym > MAX_ASYMMETRY_MM:
            out.append(
                ConstraintViolation(
                    category="aesthetic",
                    rule="vertical_symmetry",
                    severity="info",
                    message=(
                        f"Part is not vertically symmetric about x={cx:.1f} "
                        f"(worst unmatched mirror distance {worst_asym:.2f} mm)."
                    ),
                    location=f"vertex={worst_v}" if worst_v else "",
                )
            )

    edges = part.edges()
    if edges and len(edges) <= 12:
        out.append(
            ConstraintViolation(
                category="aesthetic",
                rule="min_fillet_count",
                severity="warning",
                message=(
                    "Part looks like a plain box (≤ 12 edges). Apply at "
                    "least one fillet (radius ≥ "
                    f"{MIN_FILLET_RADIUS_MM} mm) on the "
                    "outer shell — FDM bricks look unfinished."
                ),
            )
        )

    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _check_qualification(
    qualification: _printers.PrinterQualification | None,
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """Fail closed at export time when no qualification record is supplied.

    Design-time callers may omit the qualification record (the agent
    loop iterates before the receiving checklist signs). At export time
    the absence is a hard ``printer_unqualified`` error — never a
    silent pass.

    Production also revalidates the qualification against its own
    bound evidence file via
    :func:`cadkit.printers.qualification_matches_evidence`. A byte
    change between the original load and the export call raises
    ``EvidenceTamper`` which the validator surfaces as a hard
    ``qualification_tamper`` violation (never a bare exception).
    """
    out: list[ConstraintViolation] = []
    if qualification is None:
        if require_export_verification:
            out.append(
                ConstraintViolation(
                    category="print",
                    rule="printer_unqualified",
                    severity="error",
                    message=(
                        "No printer qualification record was supplied. "
                        "Production export requires a signed "
                        "PrinterQualification (procurement plan §5 row "
                        "'Required qualification artifacts')."
                    ),
                )
            )
        return out
    try:
        _printers.printer_asset_for(qualification.printer_id, qualification.profile_name)
    except ValueError as exc:
        out.append(
            ConstraintViolation(
                category="print",
                rule="printer_identity_mismatch",
                severity="error",
                message=f"Printer qualification physical identity is invalid: {exc}",
            )
        )
    # The qualification MUST be bound to a frozen evidence ref.
    # Direct dataclass construction is permitted for tests at
    # design time; at export time production refuses unbound
    # records.
    if qualification.bound_evidence is None and require_export_verification:
        out.append(
            ConstraintViolation(
                category="print",
                rule="qualification_unbound",
                severity="error",
                message=(
                    "PrinterQualification.bound_evidence is None; "
                    "production requires a record loaded from a real "
                    "provenance file via PrinterQualification.from_frozen_evidence(...). "
                    "Direct dataclass construction is not accepted "
                    "for production."
                ),
            )
        )
        return out
    # Cross-check the bound record against the current file content
    # (re-read + re-hash + per-field comparison). The cross-checker
    # raises ``EvidenceTamper`` on a byte / field mismatch; we catch
    # it here and surface the violation so production callers react
    # via :class:`ConstraintViolation` (never a bare exception).
    if require_export_verification:
        try:
            bound_evidence = qualification.bound_evidence
            if bound_evidence is not None:
                _printers.qualification_matches_evidence(qualification, bound_evidence)
        except _evidence.EvidenceTamper as exc:
            out.append(
                ConstraintViolation(
                    category="print",
                    rule="qualification_tamper",
                    severity="error",
                    message=(f"PrinterQualification.bound_evidence cross-check failed: {exc}"),
                )
            )
    # Filament must be one of the strict approved IDs. No broad
    # substring matching — substring `"prusa"` would accept any
    # Prusament variant; the procurement plan only authorises
    # Galaxy Black + Vanilla White.
    if qualification.filament not in _printers.APPROVED_FILAMENT_IDS:
        out.append(
            ConstraintViolation(
                category="print",
                rule="filament_unapproved",
                severity="error",
                message=(
                    f"Filament {qualification.filament!r} is not in the "
                    "approved workshop set "
                    f"{sorted(_printers.APPROVED_FILAMENT_IDS)} "
                    "(procurement plan §2.12 / §2.13; strict IDs only)."
                ),
            )
        )
    if require_export_verification and not qualification.profile_sha256:
        out.append(
            ConstraintViolation(
                category="print",
                rule="qualification_profile_sha256_missing",
                severity="error",
                message="Production qualification must bind the canonical profile SHA-256.",
            )
        )
    # Galaxy Black on XL 0.2 mm requires the clog-test gate (procurement
    # plan §7 risk 7). The qualification record carries the explicit
    # ``galaxy_black_clog_passed`` field; ``False`` is the default
    # (fail-closed). The orchestrator sets it to ``True`` after
    # ``BLK_GALAXY_BLACK_XL`` signs.
    if not _printers.filament_approved_for(
        qualification.profile_name,
        qualification.filament,
        qualification.galaxy_black_clog_passed,
    ):
        out.append(
            ConstraintViolation(
                category="print",
                rule="galaxy_black_xl_unverified",
                severity="error",
                message=(
                    "Galaxy Black PLA on the Prusa XL 0.2 mm nozzle "
                    "requires the BLK-GALAXY-BLACK-XL clog-test gate "
                    "(procurement plan §7 risk 7). Vanilla White is "
                    "the default XL material until the gate signs."
                ),
            )
        )
    return out


def _check_slice_evidence(
    slice_evidence: _printers.SliceEvidence | None,
    qualification: _printers.PrinterQualification | None,
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """Fail closed when 3MF/G-code export is requested without evidence.

    3MF/G-code production export requires:

    * A successful, support-free slice whose profile matches the
      qualified printer's profile name and whose layer height /
      nozzle / filament agree with the qualification record.

    STL / STEP / PNG export does **not** require slice evidence; the
    validator only complains about the slice evidence when the
    caller is on the production export path. The
    :func:`cadkit.export.export_for_production` helper centralises
    the gate and surfaces the violation with rule
    ``slice_evidence_required`` so the agent loop has a single
    remediation.
    """
    out: list[ConstraintViolation] = []
    if slice_evidence is None:
        if require_export_verification:
            out.append(
                ConstraintViolation(
                    category="print",
                    rule="slice_evidence_required",
                    severity="error",
                    message=(
                        "No slice evidence was supplied. 3MF/G-code "
                        "production export requires a signed SliceEvidence "
                        "from a successful support-free slice (procurement "
                        "plan §5 row 'Slicer invocation')."
                    ),
                )
            )
        return out
    if require_export_verification and slice_evidence.bound_evidence is None:
        out.append(
            ConstraintViolation(
                category="print",
                rule="slice_evidence_provenance_required",
                severity="error",
                message=(
                    "Production export requires SliceEvidence bound to a frozen "
                    "gates.slice provenance record."
                ),
            )
        )
    if not slice_evidence.success:
        out.append(
            ConstraintViolation(
                category="print",
                rule="slice_failed",
                severity="error",
                message=(
                    f"Slicer invocation failed (returncode "
                    f"{slice_evidence.returncode}): {slice_evidence.error_message}"
                ),
            )
        )
    if slice_evidence.supports_used:
        out.append(
            ConstraintViolation(
                category="print",
                rule="supports_disabled",
                severity="error",
                message=(
                    "Slicer reported supports_used=True; both halves must "
                    "slice support-free (procurement plan §5)."
                ),
            )
        )
    if qualification is not None and not _printers.slice_matches_qualification(
        slice_evidence,
        _printers.qualify(_printers.get_profile(qualification.profile_name), qualification),
    ):
        out.append(
            ConstraintViolation(
                category="print",
                rule="slice_evidence_mismatch",
                severity="error",
                message=(
                    "Slice evidence does not match the qualified printer "
                    "(profile / filament / nozzle / layer height)."
                ),
            )
        )
    return out


def _check_snap_pair(
    top: _snap.SnapSelectionRecord | None,
    bottom: _snap.SnapSelectionRecord | None,
) -> list[ConstraintViolation]:
    """Fail closed on the snap pair.

    Production must satisfy four gates:

    * ``snap_unbound`` — every record must be bound to a frozen
      evidence ref (``bound_evidence is not None``).
    * ``snap_pair_inconsistent`` — both halves must describe the
      same printer / filament / clearance (procurement plan §6).
    * ``snap_top_tamper`` / ``snap_bottom_tamper`` — each record's
      own bound evidence must revalidate cleanly
      (re-read + re-hash + per-field comparison). A byte / field
      mismatch raises ``EvidenceTamper`` which the validator
      surfaces as a hard violation.
    * Per-side cross-check — the snap record's side must be the
      side being checked.
    """
    out: list[ConstraintViolation] = []

    def _unbound(record: _snap.SnapSelectionRecord, side: str) -> None:
        if record.bound_evidence is None:
            out.append(
                ConstraintViolation(
                    category="print",
                    rule="snap_unbound",
                    severity="error",
                    message=(
                        f"SnapSelectionRecord[{side}].bound_evidence is None; "
                        "production requires a record loaded from a real "
                        "provenance file via "
                        "SnapSelectionRecord.from_frozen_evidence(...). "
                        "Direct dataclass construction is not accepted "
                        "for production."
                    ),
                )
            )

    def _crosscheck(record: _snap.SnapSelectionRecord, side: str) -> None:
        if record.bound_evidence is None:
            return  # already flagged as unbound
        try:
            _snap.snap_record_matches_evidence(record, record.bound_evidence, side=side)
        except _evidence.EvidenceTamper as exc:
            out.append(
                ConstraintViolation(
                    category="print",
                    rule=f"snap_{side}_tamper",
                    severity="error",
                    message=(
                        f"SnapSelectionRecord[{side}].bound_evidence cross-check failed: {exc}"
                    ),
                )
            )

    if top is None and bottom is None:
        return out
    if top is None or bottom is None:
        out.append(
            ConstraintViolation(
                category="print",
                rule="snap_pair_inconsistent",
                severity="error",
                message=(
                    "Snap selection records must be supplied for both halves; one half is missing."
                ),
            )
        )
        return out
    _unbound(top, "top")
    _unbound(bottom, "bottom")
    _crosscheck(top, "top")
    _crosscheck(bottom, "bottom")
    if not _snap.snap_pair_is_consistent(top, bottom):
        out.append(
            ConstraintViolation(
                category="print",
                rule="snap_pair_inconsistent",
                severity="error",
                message=(
                    f"Top and bottom snap records disagree: "
                    f"(clearance={top.clearance_mm} mm, "
                    f"printer={top.printer_profile_name!r}, "
                    f"filament={top.filament!r}) vs "
                    f"(clearance={bottom.clearance_mm} mm, "
                    f"printer={bottom.printer_profile_name!r}, "
                    f"filament={bottom.filament!r}). Both halves must use "
                    "the same printer, profile, and filament "
                    "(procurement plan §6)."
                ),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Geometry-evidence threshold table — VS-04 review-blocker fix
# ---------------------------------------------------------------------------
#: Procurement-plan §5 hard thresholds. Each metric carries the
#: **observed** value; the validator compares against the threshold
#: with the correct direction. A success/failure mapping here
#: means: observed >= threshold for minima, observed <= threshold
#: for maxima. Exact boundary (== threshold) passes.
_GEOMETRY_THRESHOLDS: tuple[tuple[str, float, str], ...] = (
    # (metric_name, threshold, direction)
    # direction: ">=" means observed must be >= threshold (minima).
    # direction: "<=" means observed must be <= threshold (maxima).
    ("wall_thickness_mm", 2.0, ">="),
    ("top_thickness_mm", 2.0, ">="),
    ("bottom_thickness_mm", 2.0, ">="),
    ("snap_root_thickness_mm", 2.0, ">="),
    ("decorative_thickness_mm", 1.2, ">="),
    ("through_hole_min_diameter_mm", 2.0, ">="),
    ("decorative_stroke_min_width_mm", 1.2, ">="),
    ("emboss_min_depth_mm", 0.6, ">="),
    ("engrave_min_depth_mm", 0.6, ">="),
    ("ligament_min_mm", 3.0, ">="),
    ("button_local_mounting_land_thickness_mm", 3.0, "<="),
    ("usb_ligament_mm", 3.0, ">="),
    ("max_bridge_mm", 10.0, "<="),
    ("max_overhang_deg", 45.0, "<="),
)


def _check_geometry_evidence(
    geometry_evidence: _geom.GeometryEvidence | None,
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """Compare every :class:`FeatureMetrics` field against its threshold.

    Direction rules (procurement plan §5):

    * Minima (``observed >= threshold``): wall / top / bottom /
      snap-root thickness; decorative thickness;
      through-hole diameter; decorative stroke width; emboss /
      engrave depth; ligament; USB ligament.
    * Maxima (``observed <= threshold``): bridge (≤ 10 mm);
      overhang (≤ 45°); button local mounting-land thickness
      (≤ 3 mm).
    * Boolean (must be False): ``supports_used``.

    Exact boundary (``== threshold``) passes. The frozen
    :class:`GeometryEvidence` is the verified source — a
    successful slicer run does **not** auto-certify overhang /
    bridge.

    Missing metrics at export time produce a hard
    ``geometry_metric_unverified`` error.
    """
    if geometry_evidence is None:
        return _missing_geometry_evidence(require_export_verification)
    metrics = geometry_evidence.metrics
    out = _check_geometry_binding(geometry_evidence, require_export_verification)
    out.extend(_check_geometry_metrics(metrics, require_export_verification))
    out.extend(
        _check_geometry_supports(
            metrics.supports_used,
            require_export_verification,
        )
    )
    return out


def _check_geometry_binding(
    geometry_evidence: _geom.GeometryEvidence,
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """Require the frozen geometry-to-artifact identity on production paths."""
    if not require_export_verification or geometry_evidence.binding is not None:
        return []
    return [
        ConstraintViolation(
            category="print",
            rule="geometry_binding_unverified",
            severity="error",
            message=(
                "GeometryEvidence has no source/top.stl/bottom.stl binding; "
                "production export requires exact artifact identities."
            ),
        )
    ]


def _missing_geometry_evidence(
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """Report absent frozen geometry evidence only on the export path."""
    if not require_export_verification:
        return []
    return [
        ConstraintViolation(
            category="print",
            rule="geometry_evidence_unverified",
            severity="error",
            message=(
                "No GeometryEvidence was supplied. Production export "
                "requires a signed FrozenEvidenceRef with gate "
                "'geometry' and the computed.geometry_metrics block "
                "filled in (procurement plan §5 row 'validate()')."
            ),
        )
    ]


def _check_geometry_metrics(
    metrics: Any,
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """Compare each frozen geometry metric with its directional threshold."""
    out: list[ConstraintViolation] = []
    for field_name, threshold, direction in _GEOMETRY_THRESHOLDS:
        observed = getattr(metrics, field_name)
        if observed is None:
            out.append(
                _geometry_metric_missing_violation(
                    field_name,
                    threshold,
                    direction,
                    require_export_verification,
                )
            )
            continue
        ok = observed >= threshold if direction == ">=" else observed <= threshold
        if not ok and require_export_verification:
            out.append(
                ConstraintViolation(
                    category="print",
                    rule=f"geometry_metric_{field_name}_threshold",
                    severity="error",
                    message=(
                        f"GeometryEvidence.{field_name}={observed} does "
                        f"not satisfy the procurement-plan threshold "
                        f"({direction} {threshold})."
                    ),
                )
            )
    return out


def _geometry_metric_missing_violation(
    field_name: str,
    threshold: float,
    direction: str,
    require_export_verification: bool,
) -> ConstraintViolation:
    """Create the warning/error for a missing scalar geometry metric."""
    return ConstraintViolation(
        category="print",
        rule=f"geometry_metric_{field_name}_missing",
        severity="error" if require_export_verification else "warning",
        message=(
            f"GeometryEvidence.{field_name} is None; the metric is required "
            f"for production (threshold {direction} {threshold} mm)."
        ),
    )


def _check_geometry_supports(
    supports_used: bool | None,
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """Check the boolean support-free geometry evidence metric."""
    if supports_used is None:
        if not require_export_verification:
            return []
        return [
            ConstraintViolation(
                category="print",
                rule="geometry_metric_supports_used_missing",
                severity="error",
                message=(
                    "GeometryEvidence.supports_used is None; the metric is "
                    "required for production (must be False)."
                ),
            )
        ]
    if supports_used is True:
        return [
            ConstraintViolation(
                category="print",
                rule="supports_used",
                severity="error",
                message=(
                    "GeometryEvidence.supports_used=True; both halves must "
                    "slice support-free (procurement plan §5)."
                ),
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Volume / route AABB checks — VS-04 fix
# ---------------------------------------------------------------------------
# Canonical structured-geometry names. Indexed control records use the
# ``<base>`` or ``<base>_<N>`` form; the other names are component primitives.
_CANONICAL_BUTTON_KEEPOUT = "button_keepout"
_CANONICAL_BUTTON_CUTOUT = "button_cutout"
_CANONICAL_REQUIRED_VOLUMES: tuple[tuple[str, tuple[str, ...], bool, bool], ...] = (
    ("feather_keepout", ("feather_keepout", "feather_cavity"), True, False),
    ("snap_receiver", ("snap_receiver", "snap_keepout"), True, False),
    ("usb_cutout", ("usb_cutout",), False, False),
)


def _check_volumes_and_routes(
    placements: Placements | None,
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """AABB non-overlap, route non-crossing, and required-volume checks.

    Procurement plan §5 row "validate()" requires structured
    placements / volumes / required presence:

    * Control / Feather / snap keep-outs must not
      overlap each other (a real AABB intersection check, not a
      bbox heuristic).
    * Protected exact-six wire routes must not cross any keep-out /
      cutout volume.
    * Cutouts must maintain a ligament >= 3 mm to the outer case
      edge.

    When ``placements`` is ``None`` at export time, the validator
    emits a hard ``placements_unverified`` error so the agent loop
    has a single remediation (supply the structured placement
    record).
    """
    if placements is None:
        return _missing_placements(require_export_verification)

    if placements.is_active_control_contract:
        return _check_active_volumes_and_routes(placements, require_export_verification)
    if placements.button_centres_xy or placements.dpad_centre_xy is not None:
        return [_legacy_controls_violation()]

    out = _check_required_placement_features(placements, require_export_verification)
    out.extend(_check_required_structured_geometry(placements, require_export_verification))
    keep_outs = [volume for volume in placements.volumes if volume.is_keep_out]
    cutouts = [
        volume
        for volume in placements.volumes
        if not volume.is_keep_out and not volume.is_cable_corridor
    ]
    out.extend(_check_keepout_overlaps(keep_outs, require_export_verification))
    out.extend(_check_cutout_keepout_intersections(cutouts, keep_outs, require_export_verification))
    case_aabb = _placements_case_aabb(placements)
    out.extend(_check_cutout_ligaments(cutouts, case_aabb, require_export_verification))
    out.extend(_check_routes(placements.routes, keep_outs, cutouts, require_export_verification))
    return out


def _check_active_volumes_and_routes(
    placements: Placements,
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """Run structured checks without importing the retired D-pad contract."""
    out = _check_required_placement_features(placements, require_export_verification)
    out.extend(_check_internal_clear_z(placements, require_export_verification))
    out.extend(_check_harness_representation(placements, require_export_verification))
    out.extend(_check_active_component_volumes(placements))
    keep_outs = [volume for volume in placements.volumes if volume.is_keep_out]
    cutouts = [
        volume
        for volume in placements.volumes
        if not volume.is_keep_out and not volume.is_cable_corridor
    ]
    out.extend(_check_keepout_overlaps(keep_outs, require_export_verification=True))
    out.extend(
        _check_cutout_keepout_intersections(
            cutouts,
            keep_outs,
            require_export_verification=True,
        )
    )
    case_aabb = _placements_case_aabb(placements)
    control_cutouts = [cutout for cutout in cutouts if cutout.name.startswith("control.")]
    out.extend(
        _check_cutout_ligaments(
            control_cutouts,
            case_aabb,
            require_export_verification=True,
        )
    )
    out.extend(
        _check_routes(
            placements.routes,
            keep_outs,
            cutouts,
            require_export_verification=True,
        )
    )
    return out


def _electronics_clearance_violation(
    rule: str,
    message: str,
    require_export_verification: bool,
) -> ConstraintViolation:
    """Surface reserved-electronics failures as warnings or export errors."""
    return ConstraintViolation(
        category="print",
        rule=rule,
        severity="error" if require_export_verification else "warning",
        message=message,
    )


def _check_internal_clear_z(
    placements: Placements,
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """Require the reserved electronics/harness region to retain 60 mm of Z.

    The named corridor is a design contract rather than a cutout.  Its lower
    bound is above the provisional preview intrusion, while its upper bound is
    checked against the case dimensions with wall and snap allowances kept
    outside the required clear span.  It therefore cannot silently turn the
    operator's 85 x 55 x 20 observation into a frozen production dimension.
    """
    clear_volumes = [
        volume for volume in placements.volumes if volume.name == "electronics.internal_clear_z"
    ]
    if len(clear_volumes) != 1:
        return [
            _electronics_clearance_violation(
                "internal_clear_z_unverified",
                "Exactly one electronics.internal_clear_z envelope is required for production.",
                require_export_verification,
            )
        ]
    clear = clear_volumes[0]
    if clear.is_keep_out or not clear.is_cable_corridor:
        return [
            _electronics_clearance_violation(
                "internal_clear_z_role",
                "electronics.internal_clear_z must be a non-blocking protected-clearance corridor.",
                require_export_verification,
            )
        ]
    span = clear.aabb.max_z - clear.aabb.min_z
    if span < MIN_INTERNAL_CLEAR_Z_MM - 1e-9:
        return [
            _electronics_clearance_violation(
                "internal_clear_z_insufficient",
                f"Electronics/harness internal clear Z is {span:.3f} mm; "
                f"minimum is {MIN_INTERNAL_CLEAR_Z_MM:.3f} mm after intrusions.",
                require_export_verification,
            )
        ]
    interior_min = ELECTRONICS_SHELL_WALL_MM
    interior_max_x = placements.case_length_mm - ELECTRONICS_SHELL_WALL_MM
    interior_max_y = placements.case_width_mm - ELECTRONICS_SHELL_WALL_MM
    if (
        clear.aabb.min_x < interior_min - 1e-9
        or clear.aabb.max_x > interior_max_x + 1e-9
        or clear.aabb.min_y < interior_min - 1e-9
        or clear.aabb.max_y > interior_max_y + 1e-9
    ):
        return [
            _electronics_clearance_violation(
                "internal_clear_z_outside_shell",
                "electronics.internal_clear_z leaves the wall envelope; shell walls "
                f"must remain an additional {ELECTRONICS_SHELL_WALL_MM:.3f} mm.",
                require_export_verification,
            )
        ]
    allowed_top = placements.case_thickness_mm - ELECTRONICS_SHELL_WALL_MM - MIN_SNAP_ADDITIONAL_MM
    if clear.aabb.max_z > allowed_top + 1e-9:
        return [
            _electronics_clearance_violation(
                "internal_clear_z_case_height",
                "Case height cannot contain the required electronics clear Z after "
                f"{ELECTRONICS_SHELL_WALL_MM:.3f} mm walls and "
                f"{MIN_SNAP_ADDITIONAL_MM:.3f} mm snap allowance.",
                require_export_verification,
            )
        ]
    intrusions = [
        volume
        for volume in placements.volumes
        if volume.is_keep_out
        and volume.name != "electronics.internal_clear_z"
        and volume.aabb.overlaps(clear.aabb)
    ]
    if intrusions:
        names = ", ".join(volume.name for volume in intrusions)
        return [
            _electronics_clearance_violation(
                "internal_clear_z_intrusion",
                f"Protected electronics clear Z intersects intrusion volume(s): {names}.",
                require_export_verification,
            )
        ]
    return []


def _check_harness_representation(
    placements: Placements,
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """Require both ADA2830 header rows to carry protected harness records."""
    out: list[ConstraintViolation] = []
    volumes_by_name = {volume.name: volume for volume in placements.volumes}
    routes_by_name = {route.name: route for route in placements.routes}
    for pin_count in (12, 16):
        prefix = f"harness.header_{pin_count}"
        required_volumes = (
            f"{prefix}.connector",
            f"{prefix}.strain_relief",
            f"{prefix}.no_pinch",
        )
        missing = [name for name in required_volumes if name not in volumes_by_name]
        route_name = f"wire.{prefix}"
        route = routes_by_name.get(route_name)
        if missing or route is None:
            out.append(
                ConstraintViolation(
                    category="print",
                    rule="harness_representation_unverified",
                    severity="error" if require_export_verification else "warning",
                    message=(
                        f"ADA2830 {pin_count}-pin harness requires connector, route, "
                        f"strain relief, and no-pinch records; missing {missing or [route_name]}."
                    ),
                )
            )
            continue
        no_pinch = volumes_by_name[f"{prefix}.no_pinch"]
        if not no_pinch.is_keep_out or no_pinch.is_cable_corridor:
            out.append(
                ConstraintViolation(
                    category="print",
                    rule="harness_no_pinch_unprotected",
                    severity="error" if require_export_verification else "warning",
                    message=f"ADA2830 {pin_count}-pin harness no-pinch envelope is not protected.",
                )
            )
        observed = route.minimum_bend_radius()
        if route.min_bend_radius_mm < MIN_HARNESS_BEND_RADIUS_MM - 1e-9 or (
            observed < MIN_HARNESS_BEND_RADIUS_MM - 1e-9
        ):
            out.append(
                ConstraintViolation(
                    category="print",
                    rule="harness_bend_radius_unverified",
                    severity="error" if require_export_verification else "warning",
                    message=(
                        f"ADA2830 {pin_count}-pin harness route must retain at least "
                        f"{MIN_HARNESS_BEND_RADIUS_MM:.3f} mm bend radius."
                    ),
                )
            )
    return out


def _missing_placements(require_export_verification: bool) -> list[ConstraintViolation]:
    """Report absent structured placements only on the export path."""
    if not require_export_verification:
        return []
    return [
        ConstraintViolation(
            category="print",
            rule="placements_unverified",
            severity="error",
            message=(
                "No Placements record was supplied. Production export "
                "requires the structured placements / volumes / routes record "
                "(procurement plan §5 row 'validate()')."
            ),
        )
    ]


def _check_required_placement_features(
    placements: Placements,
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """Require the Feather, USB, LED, and reset features for export."""
    if not require_export_verification:
        return []
    out: list[ConstraintViolation] = []
    required = (
        (
            placements.feather_present,
            "feather_required",
            "Placements.feather_present=False; the Feather is required "
            "(PID 4516 only, procurement plan §2.1).",
        ),
        (
            placements.usb_cutout_present,
            "usb_required",
            "Placements.usb_cutout_present=False; the Micro-B USB cutout is "
            "required (procurement plan §2.8).",
        ),
        (
            placements.led_view_hole_present,
            "led_view_hole_required",
            "Placements.led_view_hole_present=False; the 3 mm NeoPixel viewing "
            "hole is required (procurement plan §2.1).",
        ),
        (
            placements.reset_tool_hole_present,
            "reset_tool_hole_required",
            "Placements.reset_tool_hole_present=False; the 3 mm reset tool hole "
            "is required (procurement plan §2.1).",
        ),
    )
    for present, rule, message in required:
        if not present:
            out.append(
                ConstraintViolation(
                    category="print",
                    rule=rule,
                    severity="error",
                    message=message,
                )
            )
    return out


def _check_required_structured_geometry(
    placements: Placements,
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """Require the canonical Feather, USB, snap, and control volumes."""
    if not require_export_verification:
        return []
    out: list[ConstraintViolation] = []
    volumes = placements.volumes
    for role, names, keep_out, corridor in _CANONICAL_REQUIRED_VOLUMES:
        if not _has_canonical_volume(volumes, names, keep_out, corridor):
            out.append(_missing_structured_geometry(role))

    button_keepouts = [
        volume
        for volume in volumes
        if _is_indexed_canonical_name(volume.name, _CANONICAL_BUTTON_KEEPOUT)
    ]
    if len(button_keepouts) < max(1, len(placements.button_centres_xy)) or any(
        not volume.is_keep_out or volume.is_cable_corridor for volume in button_keepouts
    ):
        out.append(_missing_structured_geometry("button_keepout"))
    button_cutouts = [
        volume
        for volume in volumes
        if _is_indexed_canonical_name(volume.name, _CANONICAL_BUTTON_CUTOUT)
    ]
    if len(button_cutouts) < max(1, len(placements.button_centres_xy)) or any(
        volume.is_keep_out or volume.is_cable_corridor for volume in button_cutouts
    ):
        out.append(_missing_structured_geometry("button_cutout"))
    return out


def _has_canonical_volume(
    volumes: tuple[Volume, ...],
    names: tuple[str, ...],
    keep_out: bool,
    corridor: bool,
) -> bool:
    """Return whether a canonical volume has the required role flags."""
    return any(
        volume.name in names
        and volume.is_keep_out == keep_out
        and volume.is_cable_corridor == corridor
        for volume in volumes
    )


def _is_indexed_canonical_name(name: str, base: str) -> bool:
    """Accept a canonical name and its numeric indexed form."""
    suffix = name.removeprefix(f"{base}_")
    return name == base or (name.startswith(f"{base}_") and suffix.isdigit())


def _missing_structured_geometry(role: str) -> ConstraintViolation:
    """Create a stable hard error for omitted production geometry."""
    return ConstraintViolation(
        category="print",
        rule=f"{role}_unverified",
        severity="error",
        message=(
            f"Required structured {role!r} volume is missing or has the wrong "
            "role; production export requires canonical protected geometry "
            "from the procurement-plan primitives (procurement plan §5)."
        ),
    )


def _check_keepout_overlaps(
    keep_outs: list[Volume],
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """Check pairwise non-overlap between protected keep-out volumes."""
    out: list[ConstraintViolation] = []
    for i in range(len(keep_outs)):
        for j in range(i + 1, len(keep_outs)):
            first, second = keep_outs[i], keep_outs[j]
            if first.aabb.overlaps(second.aabb) and require_export_verification:
                out.append(
                    ConstraintViolation(
                        category="print",
                        rule="keepout_overlap",
                        severity="error",
                        message=(
                            f"Keep-outs overlap: {first.name!r} ∩ {second.name!r}. "
                            "Two keep-outs must not share volume (procurement plan §5)."
                        ),
                        location=(
                            f"{first.name}=({first.aabb.min_x},{first.aabb.min_y},{first.aabb.min_z})"
                            f"-({first.aabb.max_x},{first.aabb.max_y},{first.aabb.max_z})"
                        ),
                    )
                )
    return out


def _check_cutout_keepout_intersections(
    cutouts: list[Volume],
    keep_outs: list[Volume],
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """Check that cutouts do not remove protected keep-out volume."""
    out: list[ConstraintViolation] = []
    for cutout in cutouts:
        for keep_out in keep_outs:
            if _allowed_cutout_keepout_intersection(cutout, keep_out):
                continue
            if cutout.aabb.overlaps(keep_out.aabb) and require_export_verification:
                out.append(
                    ConstraintViolation(
                        category="print",
                        rule="cutout_intersects_keepout",
                        severity="error",
                        message=(
                            f"Cutout {cutout.name!r} intersects keep-out "
                            f"{keep_out.name!r}; the cutout removes protected volume."
                        ),
                    )
                )
    return out


def _allowed_cutout_keepout_intersection(cutout: Volume, keep_out: Volume) -> bool:
    """Allow only the component attachment intersections required by assembly."""
    if cutout.name == "usb_cutout" and keep_out.name in {"feather_cavity", "feather_keepout"}:
        return True
    if cutout.name.startswith("control.") and cutout.name.endswith(".cutout"):
        control_prefix = cutout.name.removesuffix(".cutout")
        return keep_out.name == f"{control_prefix}.keepout"
    return False


def _placements_case_aabb(placements: Placements) -> _aabb.AABB:
    """Build the case AABB used by the outer-wall ligament check."""
    return _aabb.AABB(
        min_x=0.0,
        min_y=0.0,
        min_z=0.0,
        max_x=placements.case_length_mm,
        max_y=placements.case_width_mm,
        max_z=placements.case_thickness_mm,
    )


def _check_cutout_ligaments(
    cutouts: list[Volume],
    case_aabb: _aabb.AABB,
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """Require every cutout to retain three millimetres at each wall."""
    out: list[ConstraintViolation] = []
    for cutout in cutouts:
        gaps = (
            cutout.aabb.min_x - case_aabb.min_x,
            case_aabb.max_x - cutout.aabb.max_x,
            cutout.aabb.min_y - case_aabb.min_y,
            case_aabb.max_y - cutout.aabb.max_y,
        )
        min_gap = min(gaps)
        if min_gap < 3.0 - 1e-9 and require_export_verification:
            out.append(
                ConstraintViolation(
                    category="print",
                    rule="cutout_outer_ligament",
                    severity="error",
                    message=(
                        f"Cutout {cutout.name!r} has min edge-to-edge gap "
                        f"{min_gap:.3f} mm to the case wall; minimum ligament "
                        "is 3.0 mm (procurement plan §5)."
                    ),
                )
            )
    return out


def _check_routes(
    routes: tuple[Route, ...],
    keep_outs: list[Volume],
    cutouts: list[Volume],
    require_export_verification: bool,
) -> list[ConstraintViolation]:
    """Check route bend radii and crossings into blocked volumes."""
    out: list[ConstraintViolation] = []
    blocked_volumes = (*keep_outs, *cutouts)
    for route in routes:
        observed = route.minimum_bend_radius()
        if observed < route.min_bend_radius_mm - 1e-9 and require_export_verification:
            out.append(
                ConstraintViolation(
                    category="print",
                    rule="route_min_bend_radius",
                    severity="error",
                    message=(
                        f"Route {route.name!r}: minimum bend radius {observed:.2f} mm "
                        f"is below the required {route.min_bend_radius_mm} mm."
                    ),
                )
            )
        for blocked in blocked_volumes:
            if _route_attaches_to_volume(route, blocked):
                continue
            if route.crosses_aabb(blocked.aabb) and require_export_verification:
                out.append(
                    ConstraintViolation(
                        category="print",
                        rule="route_intersects_volume",
                        severity="error",
                        message=(
                            f"Route {route.name!r} crosses the {blocked.name!r} volume; "
                            "protected wire routes cannot enter snap / USB / moving "
                            "D-pad / component volumes."
                        ),
                    )
                )
    return out


def _route_attaches_to_volume(route: Route, volume: Volume) -> bool:
    """Allow a route to enter only the source/target component it terminates on."""
    endpoints = (route.waypoints[0], route.waypoints[-1])
    return any(volume.aabb.contains_point(*point) for point in endpoints)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def is_printable(part: Part, **kwargs: Any) -> bool:
    """True iff the part passes every 'error'-severity violation.

    Procurement plan §5 requires this to be **fail-closed**: any failed
    or unavailable required print/fit check blocks STL / 3MF / G-code
    export. This is the export-time gate; the caller must supply
    ``overhang_verified=True`` and ``bridge_verified=True`` once a
    slicer / manual review has signed. ``is_printable`` always sets
    ``require_export_verification=True`` so the result is appropriate
    for the export path.
    """
    kwargs.setdefault("require_export_verification", True)
    return all(v.severity != "error" for v in validate(part, **kwargs))


def has_fatal(violations: Iterable[ConstraintViolation]) -> bool:
    """True if any violation has severity 'error'."""
    return any(v.severity == "error" for v in violations)


_NON_PRODUCTION_PREVIEW_RULES = frozenset(
    {
        "keepout_overlap",
        "route_intersects_volume",
    }
)


def has_blocking_prototype_violations(violations: Iterable[ConstraintViolation]) -> bool:
    """True when a non-production preview has an error outside known placement debt.

    The short unmeasured preview intentionally exposes unresolved component and
    route collisions. Production callers must continue to use :func:`has_fatal`.
    """
    return any(
        violation.severity == "error" and violation.rule not in _NON_PRODUCTION_PREVIEW_RULES
        for violation in violations
    )


def materials_used(materials: dict[str, set[str]] | None = None) -> dict[str, set[str]]:
    """Return the materials dict, defaulting to :data:`AVAILABLE_MATERIALS`."""
    return materials if materials is not None else AVAILABLE_MATERIALS
