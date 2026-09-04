"""Canonical case-local assembly scene shared by the editor and CAD example.

The scene is deliberately small and boring: it is a serializable record of
the assembly, not a second geometry kernel.  Positions are always expressed
in a lower-left case-local frame.  Build123d's XY plane is centred on the
case, so the adapter in this module is the only place where that frame shift
is performed.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from .snap_geometry import AUTHORITATIVE_SNAP_GEOMETRY

Point3 = tuple[float, float, float]
SCHEMA_NAME = "cadkit.assembly"
SCHEMA_VERSION = "1.0"
MAX_ASSEMBLY_NODES = 128
MAX_ASSEMBLY_ROUTES = 256
MAX_ASSEMBLY_BYTES = 256 * 1024
MAX_ASSEMBLY_PORTS_PER_NODE = 64
MAX_ASSEMBLY_WAYPOINTS = 128
MAX_ASSEMBLY_LIST_ITEMS = 512
MAX_ASSEMBLY_OBJECT_KEYS = 64
MAX_ASSEMBLY_STRING = 256
MAX_ASSEMBLY_NUMBER_ABS = 1_000_000
MAX_ASSEMBLY_DEPTH = 16
MAX_CASE_DIMENSION_MM = 1_000.0
MAX_COMPONENT_DIMENSION_MM = 1_000.0
MAX_POSITION_MM = 10_000.0
MAX_ROTATION_DEG = 36_000.0
MAX_HEADER_ROWS = 32
MAX_HEADER_PINS = 64
MAX_MOUNTING_HOLES = 32
MAX_FIELD_COUNT = 256

ELECTRONICS_BACKBONE_IDENTITY = "The Pi Hut Half-Size Breadboard - White"
ELECTRONICS_BACKBONE_SUPPLIER_SKU = "100058"
ELECTRONICS_BACKBONE_FEATHER_CONNECTION = "ADA2830"
ELECTRONICS_BACKBONE_HEADER_PIN_COUNTS = (12, 16)
ELECTRONICS_BACKBONE_TOPOLOGY_POINTS = 400
ELECTRONICS_BACKBONE_TOPOLOGY_RAIL_POINTS = (50, 50)
ELECTRONICS_BACKBONE_TOPOLOGY_GRID = "30x10"
ELECTRONICS_BACKBONE_PITCH_MM = 2.54
ELECTRONICS_BACKBONE_RETENTION_METHOD = "factory_adhesive_direct_to_inside_bottom_shell"
ELECTRONICS_BACKBONE_BOND_SURFACE = "inside_bottom_shell"
ELECTRONICS_BACKBONE_STATUS_UNMEASURED = "UNMEASURED"
ELECTRONICS_BACKBONE_STATUS_FROZEN = "FROZEN"

# The breadboard stays aligned with the case long axis. The Feather retains the
# additional quarter-turn from the pre-MIG-03 scene so USB faces left. These
# values are scene facts, not receiving dimensions.
ELECTRONICS_ASSEMBLY_ROTATION_DELTA_DEG = 90.0
ELECTRONICS_BREADBOARD_ROTATION_DEG = 0.0
ELECTRONICS_FEATHER_ROTATION_DEG = 180.0
ELECTRONICS_USB_FACING = "left"
ELECTRONICS_MIN_INTERNAL_CLEAR_Z_MM = 60.0
ELECTRONICS_SHELL_WALL_MM = 2.0
ELECTRONICS_SNAP_ADDITIONAL_MM = 2.0
ELECTRONICS_HARNESS_MIN_BEND_RADIUS_MM = 4.0
ELECTRONICS_FEATHER_ORIENTATION = "long_axis_-X_usb_facing_left"

# Preview-only operator-reported installed envelope for SKU 100058. The
# 2026-09-01 ruler observation includes adhesive, breadboard, ADA2830/header
# assembly, and Feather. It must never populate ElectronicsBackbone or
# production placement geometry.
ELECTRONICS_BACKBONE_PREVIEW_DIMENSIONS_MM = (85.0, 55.0, 20.0)
# The preview stack is centred across the case width with its long axis along
# case X. This is still a preview placement: it is never copied into the strict
# frozen receiving record.
ELECTRONICS_BACKBONE_PREVIEW_POSITION_MM = (45.0, 45.0, 10.0)
ELECTRONICS_BACKBONE_PRODUCT_URL = "https://thepihut.com/products/raspberry-pi-breadboard-half-size"
# Retained as a public compatibility constant for MIG-02 consumers. It is no
# longer emitted as the preview basis because MIG-03 uses the operator-reported
# envelope documented in the session observations.
ELECTRONICS_BACKBONE_PREVIEW_BASIS_URL = (
    "https://www.switchelectronics.co.uk/products/400-point-solderless-pcb-breadboard"
)
ELECTRONICS_BACKBONE_GROUND_BRIDGE_ROUTE_ID = "wire.feather_ground_rail"

# MIG-02 active controls.  Keep these tuples ordered: the order is shared by
# the assembly, placements artifact, editor, and firmware lanes.
CONTROL_IDS = ("up", "down", "right", "left", "action_a", "action_b")
CONTROL_ROLES = ("UP", "DOWN", "RIGHT", "LEFT", "ACTION_A", "ACTION_B")
CONTROL_COMPONENT_IDS = (
    "pbs33b_directional",
    "pbs33b_directional",
    "pbs33b_directional",
    "pbs33b_directional",
    "guuzi_b09bmrdptn_action",
    "guuzi_b09bmrdptn_action",
)
CONTROL_KINDS = (
    "pbs33b_button",
    "pbs33b_button",
    "pbs33b_button",
    "pbs33b_button",
    "guuzi_action_button",
    "guuzi_action_button",
)
CONTROL_GPIOS = ("D5", "D6", "D9", "D10", "D11", "D12")
ACTIVE_CONTROL_COUNT = len(CONTROL_IDS)
ControlMetrics = tuple[
    float,
    tuple[float, float],
    tuple[float, float, float],
    tuple[float, float, float],
    float,
]


def _provisional_control_metrics(component_id: str) -> ControlMetrics:
    from .parametric import (
        BUTTON_ROUTE_MIN_BEND_RADIUS_PROVISIONAL_MM,
        GUUZI_CUTOUT_PROVISIONAL_MM,
        GUUZI_MOUNTING_LAND_PROVISIONAL_MM,
        GUUZI_TERMINAL_KEEPOUT_PROVISIONAL_MM,
        GUUZI_UNDERSIDE_KEEPOUT_PROVISIONAL_MM,
        PBS33B_CUTOUT_PROVISIONAL_MM,
        PBS33B_MOUNTING_LAND_PROVISIONAL_MM,
        PBS33B_TERMINAL_KEEPOUT_PROVISIONAL_MM,
        PBS33B_UNDERSIDE_KEEPOUT_PROVISIONAL_MM,
    )

    if component_id == CONTROL_COMPONENT_IDS[0]:
        return (
            PBS33B_CUTOUT_PROVISIONAL_MM,
            PBS33B_MOUNTING_LAND_PROVISIONAL_MM,
            PBS33B_UNDERSIDE_KEEPOUT_PROVISIONAL_MM,
            PBS33B_TERMINAL_KEEPOUT_PROVISIONAL_MM,
            BUTTON_ROUTE_MIN_BEND_RADIUS_PROVISIONAL_MM,
        )
    if component_id == CONTROL_COMPONENT_IDS[-1]:
        return (
            GUUZI_CUTOUT_PROVISIONAL_MM,
            GUUZI_MOUNTING_LAND_PROVISIONAL_MM,
            GUUZI_UNDERSIDE_KEEPOUT_PROVISIONAL_MM,
            GUUZI_TERMINAL_KEEPOUT_PROVISIONAL_MM,
            BUTTON_ROUTE_MIN_BEND_RADIUS_PROVISIONAL_MM,
        )
    raise ValueError(f"unsupported active control component: {component_id!r}")


def _point3(value: Sequence[float]) -> Point3:
    if len(value) != 3:
        raise ValueError(f"3D point requires three values, got {value!r}")
    result = tuple(float(part) for part in value)
    if any(not math.isfinite(part) or abs(part) > MAX_POSITION_MM for part in result):
        raise ValueError("assembly points must be finite and within the position limit")
    return result  # type: ignore[return-value]


def _rotation3(value: Sequence[float]) -> Point3:
    if len(value) != 3:
        raise ValueError(f"rotation requires three values, got {value!r}")
    result = tuple(float(part) for part in value)
    if any(not math.isfinite(part) or abs(part) > MAX_ROTATION_DEG for part in result):
        raise ValueError("assembly rotations must be finite and within the rotation limit")
    return result  # type: ignore[return-value]


def _dimension_value(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a finite positive millimetre value") from exc
    if not math.isfinite(result) or result <= 0 or result > MAX_COMPONENT_DIMENSION_MM:
        raise ValueError(f"{label} must be a finite positive millimetre value")
    return result


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_ASSEMBLY_STRING:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _optional_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) > MAX_ASSEMBLY_STRING:
        raise ValueError(f"{label} must be a string")
    return value


def _bounded_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or abs(value) > MAX_FIELD_COUNT:
        raise ValueError(f"{label} must be a bounded integer")
    return value


def _positive_bounded_int(value: Any, label: str, maximum: int = MAX_FIELD_COUNT) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or abs(value) > maximum:
        raise ValueError(f"{label} must be a bounded integer")
    result = value
    if result <= 0 or result > maximum:
        raise ValueError(f"{label} must be a positive bounded integer")
    return result


def _strict_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} must be a boolean")
    return value


def _optional_positive_dimension_tuple(
    value: Sequence[float] | None, label: str
) -> tuple[float, float, float] | None:
    if value is None:
        return None
    if len(value) != 3:
        raise ValueError(f"{label} must contain three dimensions or null")
    result = tuple(_dimension_value(item, f"{label}[{index}]") for index, item in enumerate(value))
    return result  # type: ignore[return-value]


def _optional_positive_dimension(value: Any, label: str) -> float | None:
    if value is None:
        return None
    return _dimension_value(value, label)


def _optional_point3(value: Sequence[float] | None, label: str) -> Point3 | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be a three-number list or null")
    return _point3(value)


def _validate_structural_number(value: int | float, path: str) -> None:
    number = float(value)
    if not math.isfinite(number) or abs(number) > MAX_ASSEMBLY_NUMBER_ABS:
        raise ValueError(f"{path} contains an invalid number")
    count_field = path.rsplit(".", 1)[-1] in {"count", "pin_count", "header_count", "headers"}
    if count_field and (not number.is_integer() or number < 0 or number > MAX_FIELD_COUNT):
        raise ValueError(f"{path} contains an invalid count")


def _list_limit(path: str) -> int:
    limits = {
        "headers": MAX_HEADER_ROWS,
        "mounting_holes_mm": MAX_MOUNTING_HOLES,
        "mounting_holes_native_mm": MAX_MOUNTING_HOLES,
        "holes": MAX_MOUNTING_HOLES,
        "waypoints_mm": MAX_ASSEMBLY_WAYPOINTS,
        "ports": MAX_ASSEMBLY_PORTS_PER_NODE,
        "nodes": MAX_ASSEMBLY_NODES,
        "routes": MAX_ASSEMBLY_ROUTES,
    }
    return limits.get(path.rsplit(".", 1)[-1], MAX_ASSEMBLY_LIST_ITEMS)


def _validate_structural_mapping(value: Mapping[Any, Any], path: str, depth: int) -> None:
    if len(value) > MAX_ASSEMBLY_OBJECT_KEYS:
        raise ValueError(f"{path} contains too many fields")
    for key, item in value.items():
        if not isinstance(key, str) or len(key) > MAX_ASSEMBLY_STRING:
            raise ValueError(f"{path} contains an invalid field name")
        _validate_structural_value(item, path=f"{path}.{key}".strip("."), depth=depth + 1)


def _validate_structural_sequence(value: Sequence[Any], path: str, depth: int) -> None:
    if len(value) > _list_limit(path):
        raise ValueError(f"{path} contains too many list items")
    for index, item in enumerate(value):
        _validate_structural_value(item, path=f"{path}[{index}]", depth=depth + 1)


def _validate_structural_value(value: Any, *, path: str = "", depth: int = 0) -> None:
    if depth > MAX_ASSEMBLY_DEPTH:
        raise ValueError("assembly metadata is too deeply nested")
    if value is None or isinstance(value, (bool, str)):
        if isinstance(value, str) and len(value) > MAX_ASSEMBLY_STRING:
            raise ValueError(f"{path} contains an oversized string")
        return
    if isinstance(value, (int, float)):
        _validate_structural_number(value, path)
        return
    if isinstance(value, Mapping):
        _validate_structural_mapping(cast(Mapping[Any, Any], value), path, depth)
        return
    if isinstance(value, (list, tuple)):
        _validate_structural_sequence(cast(Sequence[Any], value), path, depth)
        return
    raise ValueError(f"{path} contains a non-JSON value")


def _require_exact_mapping(value: Mapping[Any, Any], expected: frozenset[str], label: str) -> None:
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} object keys must be strings")
    keys = set(value)
    if len(keys) != len(expected) or any(key not in expected for key in keys):
        raise ValueError(f"{label} has unknown or missing fields")


@dataclass(frozen=True)
class ElectronicsTopology:
    """Seller-listed breadboard topology, without physical dimensions."""

    points: int = ELECTRONICS_BACKBONE_TOPOLOGY_POINTS
    rail_point_counts: tuple[int, int] = ELECTRONICS_BACKBONE_TOPOLOGY_RAIL_POINTS
    grid: str = ELECTRONICS_BACKBONE_TOPOLOGY_GRID
    pitch_mm: float = ELECTRONICS_BACKBONE_PITCH_MM
    self_adhesive_rear: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "points",
            _positive_bounded_int(
                self.points,
                "topology.points",
                maximum=MAX_ASSEMBLY_NUMBER_ABS,
            ),
        )
        if len(self.rail_point_counts) != 2:
            raise ValueError("topology.rail_point_counts must contain two values")
        object.__setattr__(
            self,
            "rail_point_counts",
            tuple(
                _positive_bounded_int(value, f"topology.rail_point_counts[{index}]")
                for index, value in enumerate(self.rail_point_counts)
            ),
        )
        _required_text(self.grid, "topology.grid")
        object.__setattr__(self, "pitch_mm", _dimension_value(self.pitch_mm, "topology.pitch_mm"))
        _strict_bool(self.self_adhesive_rear, "topology.self_adhesive_rear")

    def to_dict(self) -> dict[str, Any]:
        return {
            "points": self.points,
            "rail_point_counts": list(self.rail_point_counts),
            "grid": self.grid,
            "pitch_mm": self.pitch_mm,
            "self_adhesive_rear": self.self_adhesive_rear,
        }

    @classmethod
    def from_dict(cls, value: Any) -> ElectronicsTopology:
        if not isinstance(value, Mapping):
            raise ValueError("electronics_backbone.topology must be an object")
        payload = cast(Mapping[str, Any], value)
        _require_exact_mapping(
            payload,
            frozenset({"points", "rail_point_counts", "grid", "pitch_mm", "self_adhesive_rear"}),
            "electronics_backbone.topology",
        )
        rails = payload["rail_point_counts"]
        if not isinstance(rails, list):
            raise ValueError("electronics_backbone.topology.rail_point_counts is malformed")
        rail_values = cast(list[Any], rails)
        if len(rail_values) != 2:
            raise ValueError("electronics_backbone.topology.rail_point_counts is malformed")
        return cls(
            points=payload["points"],
            rail_point_counts=cast(tuple[int, int], tuple(rail_values)),
            grid=payload["grid"],
            pitch_mm=payload["pitch_mm"],
            self_adhesive_rear=payload["self_adhesive_rear"],
        )


_ELECTRONICS_BACKBONE_KEYS = frozenset(
    {
        "identity",
        "supplier_sku",
        "feather_connection",
        "header_pin_counts",
        "topology",
        "status",
        "retention_method",
        "outside_dimensions_mm",
        "stack_height_mm",
        "placement_offset_mm",
        "usb_offset_mm",
        "insertion_depth_mm",
        "adhesive_bond_interface",
        "adhesive_bond_verified",
        "signed_record_binding",
    }
)
_ELECTRONICS_BACKBONE_BOND_INTERFACE_KEYS = frozenset({"surface"})


@dataclass(frozen=True)
class ElectronicsBackbone:
    """Serialized, non-geometric electronics-backbone contract.

    The active default contains seller topology and component identity only.
    All breadboard dimensions, placement offsets, insertion depth, and
    adhesive bond interface remain null until a signed receiving record freezes
    them. A separate scene node may provide an explicitly provisional browser
    preview, but its operator-reported dimensions never enter this record.
    """

    identity: str = ELECTRONICS_BACKBONE_IDENTITY
    supplier_sku: str = ELECTRONICS_BACKBONE_SUPPLIER_SKU
    feather_connection: str = ELECTRONICS_BACKBONE_FEATHER_CONNECTION
    header_pin_counts: tuple[int, int] = ELECTRONICS_BACKBONE_HEADER_PIN_COUNTS
    topology: ElectronicsTopology = field(default_factory=ElectronicsTopology)
    status: str = ELECTRONICS_BACKBONE_STATUS_UNMEASURED
    retention_method: str = ELECTRONICS_BACKBONE_RETENTION_METHOD
    outside_dimensions_mm: tuple[float, float, float] | None = None
    stack_height_mm: float | None = None
    placement_offset_mm: Point3 | None = None
    usb_offset_mm: Point3 | None = None
    insertion_depth_mm: float | None = None
    adhesive_bond_interface: Mapping[str, Any] | None = None
    adhesive_bond_verified: bool = False
    signed_record_binding: str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.identity, "electronics_backbone.identity"),
            (self.supplier_sku, "electronics_backbone.supplier_sku"),
            (self.feather_connection, "electronics_backbone.feather_connection"),
            (self.status, "electronics_backbone.status"),
            (self.retention_method, "electronics_backbone.retention_method"),
        ):
            _required_text(value, label)
        if self.retention_method != ELECTRONICS_BACKBONE_RETENTION_METHOD:
            raise ValueError(
                "electronics_backbone.retention_method does not match the active contract"
            )
        if len(self.header_pin_counts) != 2:
            raise ValueError("electronics_backbone.header_pin_counts must contain two values")
        object.__setattr__(
            self,
            "header_pin_counts",
            tuple(
                _positive_bounded_int(value, f"electronics_backbone.header_pin_counts[{index}]")
                for index, value in enumerate(self.header_pin_counts)
            ),
        )
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.topology, ElectronicsTopology
        ):
            raise ValueError("electronics_backbone.topology must be ElectronicsTopology")
        object.__setattr__(
            self,
            "outside_dimensions_mm",
            _optional_positive_dimension_tuple(
                self.outside_dimensions_mm,
                "electronics_backbone.outside_dimensions_mm",
            ),
        )
        object.__setattr__(
            self,
            "stack_height_mm",
            _optional_positive_dimension(
                self.stack_height_mm,
                "electronics_backbone.stack_height_mm",
            ),
        )
        object.__setattr__(
            self,
            "placement_offset_mm",
            _optional_point3(
                self.placement_offset_mm,
                "electronics_backbone.placement_offset_mm",
            ),
        )
        object.__setattr__(
            self,
            "usb_offset_mm",
            _optional_point3(self.usb_offset_mm, "electronics_backbone.usb_offset_mm"),
        )
        object.__setattr__(
            self,
            "insertion_depth_mm",
            _optional_positive_dimension(
                self.insertion_depth_mm,
                "electronics_backbone.insertion_depth_mm",
            ),
        )
        if self.adhesive_bond_interface is not None:
            if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
                self.adhesive_bond_interface, Mapping
            ):
                raise ValueError(
                    "electronics_backbone.adhesive_bond_interface must be an object or null"
                )
            interface = dict(self.adhesive_bond_interface)
            _validate_structural_value(
                interface, path="electronics_backbone.adhesive_bond_interface"
            )
            _require_exact_mapping(
                interface,
                _ELECTRONICS_BACKBONE_BOND_INTERFACE_KEYS,
                "electronics_backbone.adhesive_bond_interface",
            )
            if interface["surface"] != ELECTRONICS_BACKBONE_BOND_SURFACE:
                raise ValueError(
                    "electronics_backbone.adhesive_bond_interface.surface does not match "
                    "the active contract"
                )
            object.__setattr__(self, "adhesive_bond_interface", interface)
        _strict_bool(self.adhesive_bond_verified, "electronics_backbone.adhesive_bond_verified")
        if self.signed_record_binding is not None:
            _required_text(self.signed_record_binding, "electronics_backbone.signed_record_binding")

    @property
    def identity_matches_contract(self) -> bool:
        """Whether immutable seller/component facts match this workshop contract."""
        return (
            self.identity == ELECTRONICS_BACKBONE_IDENTITY
            and self.supplier_sku == ELECTRONICS_BACKBONE_SUPPLIER_SKU
            and self.feather_connection == ELECTRONICS_BACKBONE_FEATHER_CONNECTION
            and self.header_pin_counts == ELECTRONICS_BACKBONE_HEADER_PIN_COUNTS
            and self.topology
            == ElectronicsTopology(
                points=ELECTRONICS_BACKBONE_TOPOLOGY_POINTS,
                rail_point_counts=ELECTRONICS_BACKBONE_TOPOLOGY_RAIL_POINTS,
                grid=ELECTRONICS_BACKBONE_TOPOLOGY_GRID,
                pitch_mm=ELECTRONICS_BACKBONE_PITCH_MM,
                self_adhesive_rear=True,
            )
        )

    @property
    def is_frozen_for_export(self) -> bool:
        """Whether all backbone fields needed by production are populated."""
        return bool(
            self.identity_matches_contract
            and self.status == ELECTRONICS_BACKBONE_STATUS_FROZEN
            and self.retention_method == ELECTRONICS_BACKBONE_RETENTION_METHOD
            and self.outside_dimensions_mm is not None
            and self.stack_height_mm is not None
            and self.placement_offset_mm is not None
            and self.usb_offset_mm is not None
            and self.insertion_depth_mm is not None
            and self.adhesive_bond_interface
            and self.adhesive_bond_verified
            and self.signed_record_binding
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity,
            "supplier_sku": self.supplier_sku,
            "feather_connection": self.feather_connection,
            "header_pin_counts": list(self.header_pin_counts),
            "topology": self.topology.to_dict(),
            "status": self.status,
            "retention_method": self.retention_method,
            "outside_dimensions_mm": (
                list(self.outside_dimensions_mm) if self.outside_dimensions_mm is not None else None
            ),
            "stack_height_mm": self.stack_height_mm,
            "placement_offset_mm": (
                list(self.placement_offset_mm) if self.placement_offset_mm is not None else None
            ),
            "usb_offset_mm": list(self.usb_offset_mm) if self.usb_offset_mm is not None else None,
            "insertion_depth_mm": self.insertion_depth_mm,
            "adhesive_bond_interface": (
                dict(self.adhesive_bond_interface)
                if self.adhesive_bond_interface is not None
                else None
            ),
            "adhesive_bond_verified": self.adhesive_bond_verified,
            "signed_record_binding": self.signed_record_binding,
        }

    @classmethod
    def from_dict(cls, value: Any) -> ElectronicsBackbone:
        if not isinstance(value, Mapping):
            raise ValueError("electronics_backbone must be an object or null")
        payload = cast(Mapping[str, Any], value)
        _require_exact_mapping(payload, _ELECTRONICS_BACKBONE_KEYS, "electronics_backbone")
        headers = payload["header_pin_counts"]
        if not isinstance(headers, list):
            raise ValueError("electronics_backbone.header_pin_counts is malformed")
        header_values = cast(list[Any], headers)
        if len(header_values) != 2:
            raise ValueError("electronics_backbone.header_pin_counts is malformed")
        for label in ("outside_dimensions_mm", "placement_offset_mm", "usb_offset_mm"):
            item = payload[label]
            if item is not None and not isinstance(item, list):
                raise ValueError(f"electronics_backbone.{label} must be a list or null")
        return cls(
            identity=payload["identity"],
            supplier_sku=payload["supplier_sku"],
            feather_connection=payload["feather_connection"],
            header_pin_counts=cast(tuple[int, int], tuple(header_values)),
            topology=ElectronicsTopology.from_dict(payload["topology"]),
            status=payload["status"],
            retention_method=payload["retention_method"],
            outside_dimensions_mm=cast(
                tuple[float, float, float] | None,
                payload["outside_dimensions_mm"],
            ),
            stack_height_mm=payload["stack_height_mm"],
            placement_offset_mm=cast(Point3 | None, payload["placement_offset_mm"]),
            usb_offset_mm=cast(Point3 | None, payload["usb_offset_mm"]),
            insertion_depth_mm=payload["insertion_depth_mm"],
            adhesive_bond_interface=cast(
                Mapping[str, Any] | None, payload["adhesive_bond_interface"]
            ),
            adhesive_bond_verified=payload["adhesive_bond_verified"],
            signed_record_binding=payload["signed_record_binding"],
        )


def _validate_visual_number(value: Any, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")


def _validate_header_entry(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("visual.headers entries must be objects")
    header = cast(Mapping[str, Any], value)
    required_fields = ("x_mm", "y_mm", "count", "pitch_mm", "axis")
    if any(field not in header for field in required_fields):
        raise ValueError("visual.headers entries have an invalid shape")
    for coordinate_field in ("x_mm", "y_mm", "pitch_mm"):
        _validate_visual_number(header[coordinate_field], f"visual.headers.{coordinate_field}")
    count = header["count"]
    if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= MAX_HEADER_PINS:
        raise ValueError("visual header pin counts must be bounded integers")
    if not isinstance(header["axis"], str) or header["axis"] not in {"x", "y"}:
        raise ValueError("visual.headers axis must be 'x' or 'y'")
    if "id" in header and not isinstance(header["id"], str):
        raise ValueError("visual.headers id must be a string")


def _validate_header_limits(visual: Mapping[str, Any]) -> None:
    headers = visual.get("headers", [])
    if not isinstance(headers, list):
        raise ValueError("visual.headers must be a bounded list")
    for header in cast(list[Any], headers):
        _validate_header_entry(header)


def _validate_mounting_holes(visual: Mapping[str, Any]) -> None:
    holes = visual.get("mounting_holes_mm", [])
    if not isinstance(holes, list):
        raise ValueError("visual.mounting_holes_mm must be a bounded list")
    for hole in cast(list[Any], holes):
        if not isinstance(hole, (list, tuple)):
            raise ValueError("visual.mounting_holes_mm entries must be two-number sequences")
        hole_values = cast(Sequence[Any], hole)
        if len(hole_values) != 2:
            raise ValueError("visual.mounting_holes_mm entries must be two-number sequences")
        for coordinate in hole_values:
            _validate_visual_number(coordinate, "visual.mounting_holes_mm coordinate")


def _validate_node_payload(value: Mapping[str, Any]) -> None:
    visual = value.get("visual", {})
    metadata = value.get("metadata", {})
    if not isinstance(visual, Mapping) or not isinstance(metadata, Mapping):
        raise ValueError("assembly visual and metadata fields must be objects")
    visual_mapping = cast(Mapping[str, Any], visual)
    metadata_mapping = cast(Mapping[str, Any], metadata)
    _validate_structural_value(dict(visual_mapping), path="visual")
    _validate_structural_value(dict(metadata_mapping), path="metadata")
    _validate_header_limits(visual_mapping)
    _validate_mounting_holes(visual_mapping)


def _validate_scene_header(version: str, units: str, coordinate_system: str) -> None:
    if version != SCHEMA_VERSION:
        raise ValueError(f"unsupported assembly schema version: {version!r}")
    if units != "mm":
        raise ValueError("assembly units must be 'mm'")
    if coordinate_system != "case_local_lower_left_xyz":
        raise ValueError("assembly coordinate system is not canonical")


def _validate_case_dimensions(dimensions: Dimensions) -> None:
    if any(
        value <= 0 or value > MAX_CASE_DIMENSION_MM
        for value in (dimensions.x, dimensions.y, dimensions.z)
    ):
        raise ValueError("case dimensions must be finite, positive, and practical")


def _validate_scene_relationships(nodes: Sequence[AssemblyNode]) -> set[str]:
    ids = [node.id for node in nodes]
    if len(ids) != len(set(ids)):
        raise ValueError("assembly node IDs must be unique")
    node_ids = set(ids)
    for node in nodes:
        if node.parent_id and node.parent_id not in node_ids:
            raise ValueError(f"unknown parent node: {node.parent_id}")
    return node_ids


@dataclass(frozen=True)
class Dimensions:
    """Physical or protected dimensions, in millimetres."""

    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        values = tuple(
            _dimension_value(value, "assembly dimension") for value in (self.x, self.y, self.z)
        )
        object.__setattr__(self, "x", values[0])
        object.__setattr__(self, "y", values[1])
        object.__setattr__(self, "z", values[2])

    def to_dict(self) -> dict[str, float]:
        return {"x_mm": self.x, "y_mm": self.y, "z_mm": self.z}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Dimensions:
        return cls(value["x_mm"], value["y_mm"], value["z_mm"])


@dataclass(frozen=True)
class Transform:
    """A case-local centre position and Euler rotation in degrees."""

    position: Point3
    rotation_deg: Point3 = (0.0, 0.0, 0.0)

    def __post_init__(self) -> None:
        object.__setattr__(self, "position", _point3(self.position))
        object.__setattr__(self, "rotation_deg", _rotation3(self.rotation_deg))

    def to_dict(self) -> dict[str, Any]:
        return {
            "position_mm": list(self.position),
            "rotation_deg": list(self.rotation_deg),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Transform:
        return cls(
            _point3(value["position_mm"]),
            _rotation3(value.get("rotation_deg", (0.0, 0.0, 0.0))),
        )


def local_to_scene_point(transform: Transform, point: Sequence[float]) -> Point3:
    """Map a node-local point into the case-local scene frame.

    Component dimensions and manufacturer coordinates stay in their native
    local frame.  The node rotation is applied exactly once when a point is
    emitted into the scene; this is the shared rule used by ports, holes,
    headers, and wire endpoints.
    """
    px, py, pz = _point3(point)
    angle = math.radians(transform.rotation_deg[2])
    rotated = (
        px * math.cos(angle) - py * math.sin(angle),
        px * math.sin(angle) + py * math.cos(angle),
        pz,
    )
    return tuple(transform.position[index] + rotated[index] for index in range(3))  # type: ignore[return-value]


@dataclass(frozen=True)
class Port:
    """A named electrical or mechanical connection on a scene node."""

    name: str
    position: Point3
    signal: str
    direction: str = "bidirectional"

    def __post_init__(self) -> None:
        if not self.name or not self.signal:
            raise ValueError("assembly ports require a name and signal")
        _required_text(self.name, "assembly port name")
        _required_text(self.signal, "assembly port signal")
        _required_text(self.direction, "assembly port direction")
        object.__setattr__(self, "position", _point3(self.position))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "position_mm": list(self.position),
            "signal": self.signal,
            "direction": self.direction,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Port:
        return cls(
            _required_text(value["name"], "assembly port name"),
            _point3(value["position_mm"]),
            _required_text(value["signal"], "assembly port signal"),
            _optional_text(value.get("direction", "bidirectional"), "assembly port direction"),
        )


@dataclass(frozen=True)
class AssemblyNode:
    """One stable, selectable item in the assembly scene."""

    id: str
    kind: str
    transform: Transform
    physical_dimensions: Dimensions
    keep_out_dimensions: Dimensions | None
    parent_id: str | None
    mobility: str
    provenance: str
    status: str
    layer: int
    z_order: int
    visual_ref: str
    proxy_ref: str
    visual: Mapping[str, Any] = field(default_factory=lambda: cast(Mapping[str, Any], {}))
    ports: tuple[Port, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=lambda: cast(Mapping[str, Any], {}))

    def __post_init__(self) -> None:
        if not self.id or not self.kind:
            raise ValueError("assembly nodes require stable id and kind")
        if self.mobility not in {"fixed", "derived", "constrained_xy"}:
            raise ValueError(f"unsupported node mobility: {self.mobility!r}")
        object.__setattr__(self, "ports", tuple(self.ports))
        if len(self.ports) > MAX_ASSEMBLY_PORTS_PER_NODE:
            raise ValueError("assembly node has too many ports")
        if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.visual, Mapping
        ) or not isinstance(self.metadata, Mapping):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("assembly visual and metadata fields must be objects")
        _validate_structural_value(dict(self.visual), path="visual")
        _validate_structural_value(dict(self.metadata), path="metadata")
        _validate_header_limits(self.visual)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "parent_id": self.parent_id,
            "transform": self.transform.to_dict(),
            "physical_dimensions": self.physical_dimensions.to_dict(),
            "keep_out_dimensions": (
                self.keep_out_dimensions.to_dict() if self.keep_out_dimensions else None
            ),
            "mobility": self.mobility,
            "provenance": self.provenance,
            "status": self.status,
            "layer": self.layer,
            "z_order": self.z_order,
            "visual_ref": self.visual_ref,
            "proxy_ref": self.proxy_ref,
            "visual": dict(self.visual),
            "ports": [port.to_dict() for port in self.ports],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AssemblyNode:
        _validate_node_payload(value)
        keep_out = value.get("keep_out_dimensions")
        ports = value["ports"]
        if not isinstance(ports, list):
            raise ValueError("assembly node ports must be a list")
        port_values = cast(list[Any], ports)
        parent_id = value.get("parent_id")
        if parent_id is not None and not isinstance(parent_id, str):
            raise ValueError("assembly node parent_id must be a string or null")
        return cls(
            id=_required_text(value["id"], "assembly node id"),
            kind=_required_text(value["kind"], "assembly node kind"),
            transform=Transform.from_dict(value["transform"]),
            physical_dimensions=Dimensions.from_dict(value["physical_dimensions"]),
            keep_out_dimensions=Dimensions.from_dict(keep_out) if keep_out else None,
            parent_id=parent_id,
            mobility=_required_text(value["mobility"], "assembly node mobility"),
            provenance=_required_text(value["provenance"], "assembly node provenance"),
            status=_required_text(value["status"], "assembly node status"),
            layer=_bounded_int(value["layer"], "assembly node layer"),
            z_order=_bounded_int(value["z_order"], "assembly node z_order"),
            visual_ref=_optional_text(value.get("visual_ref", ""), "assembly visual_ref"),
            proxy_ref=_optional_text(value.get("proxy_ref", ""), "assembly proxy_ref"),
            visual=dict(value.get("visual", {})),
            ports=tuple(Port.from_dict(port) for port in port_values),
            metadata=dict(value.get("metadata", {})),
        )


@dataclass(frozen=True)
class WireRoute:
    """Deterministic, diagrammatic wiring between two named ports."""

    id: str
    source: str
    target: str
    waypoints: tuple[Point3, ...]
    signal: str
    provenance: str = "generated-proxy"
    status: str = "provisional"
    kind: str = "wire"
    min_bend_radius_mm: float = 1.0
    protected: bool = False
    strain_relief_ref: str | None = None
    no_pinch_envelope_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.source or not self.target:
            raise ValueError("wire routes require id, source, and target")
        if len(self.waypoints) < 2:
            raise ValueError("wire routes require at least two waypoints")
        if len(self.waypoints) > MAX_ASSEMBLY_WAYPOINTS:
            raise ValueError("wire routes exceed the waypoint limit")
        _required_text(self.id, "wire route id")
        _required_text(self.source, "wire route source")
        _required_text(self.target, "wire route target")
        _required_text(self.signal, "wire route signal")
        _dimension_value(self.min_bend_radius_mm, "wire route min_bend_radius_mm")
        _strict_bool(self.protected, "wire route protected")
        if self.strain_relief_ref is not None:
            _required_text(self.strain_relief_ref, "wire route strain_relief_ref")
        if self.no_pinch_envelope_ref is not None:
            _required_text(self.no_pinch_envelope_ref, "wire route no_pinch_envelope_ref")
        object.__setattr__(self, "waypoints", tuple(_point3(point) for point in self.waypoints))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "source": self.source,
            "target": self.target,
            "waypoints_mm": [list(point) for point in self.waypoints],
            "signal": self.signal,
            "provenance": self.provenance,
            "status": self.status,
            "min_bend_radius_mm": self.min_bend_radius_mm,
            "protected": self.protected,
            "strain_relief_ref": self.strain_relief_ref,
            "no_pinch_envelope_ref": self.no_pinch_envelope_ref,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> WireRoute:
        waypoints = value["waypoints_mm"]
        if not isinstance(waypoints, list):
            raise ValueError("wire route waypoints_mm must be a list")
        waypoint_values = cast(list[Any], waypoints)
        return cls(
            id=_required_text(value["id"], "wire route id"),
            source=_required_text(value["source"], "wire route source"),
            target=_required_text(value["target"], "wire route target"),
            waypoints=tuple(_point3(cast(Sequence[float], point)) for point in waypoint_values),
            signal=_required_text(value["signal"], "wire route signal"),
            provenance=_required_text(
                value.get("provenance", "generated-proxy"), "wire route provenance"
            ),
            status=_required_text(value.get("status", "provisional"), "wire route status"),
            kind=_required_text(value.get("kind", "wire"), "wire route kind"),
            min_bend_radius_mm=float(value.get("min_bend_radius_mm", 1.0)),
            protected=value.get("protected", False),
            strain_relief_ref=value.get("strain_relief_ref"),
            no_pinch_envelope_ref=value.get("no_pinch_envelope_ref"),
        )


@dataclass(frozen=True)
class AssemblyScene:
    """Versioned canonical scene record."""

    case_dimensions: Dimensions
    nodes: tuple[AssemblyNode, ...]
    routes: tuple[WireRoute, ...]
    version: str = SCHEMA_VERSION
    units: str = "mm"
    coordinate_system: str = "case_local_lower_left_xyz"
    electronics_backbone: ElectronicsBackbone | None = None

    def __post_init__(self) -> None:
        _validate_scene_header(self.version, self.units, self.coordinate_system)
        _validate_case_dimensions(self.case_dimensions)
        if len(self.nodes) > MAX_ASSEMBLY_NODES or len(self.routes) > MAX_ASSEMBLY_ROUTES:
            raise ValueError("assembly exceeds node or route limits")
        _validate_scene_relationships(self.nodes)
        if (
            self.electronics_backbone is not None
            and type(self.electronics_backbone) is not ElectronicsBackbone
        ):
            raise ValueError("electronics_backbone must be an ElectronicsBackbone or null")
        route_ids = [route.id for route in self.routes]
        if len(route_ids) != len(set(route_ids)):
            raise ValueError("assembly route IDs must be unique")
        object.__setattr__(self, "nodes", tuple(self.nodes))
        object.__setattr__(self, "routes", tuple(self.routes))

    def node(self, node_id: str) -> AssemblyNode:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise KeyError(node_id)

    def port_position(self, reference: str) -> Point3 | None:
        """Resolve a ``node.port`` attachment in the case-local frame."""
        node_id, separator, port_name = reference.rpartition(".")
        if not separator:
            return None
        try:
            node = self.node(node_id)
            port = next(port for port in node.ports if port.name == port_name)
        except (KeyError, StopIteration):
            return None
        return local_to_scene_point(node.transform, port.position)

    def route_waypoints(self, route: WireRoute) -> tuple[Point3, ...]:
        """Resolve route endpoints from live node-port attachments."""
        points = list(route.waypoints)
        source = self.port_position(route.source)
        target = self.port_position(route.target)
        if source is not None:
            points[0] = source
        if target is not None:
            points[-1] = target
        return tuple(points)

    def route_signal_errors(self) -> tuple[str, ...]:
        """Return route endpoint errors without mutating or repairing the scene."""
        errors: list[str] = []
        for route in self.routes:
            for reference in (route.source, route.target):
                error = self._route_signal_error(route, reference)
                if error is not None:
                    errors.append(error)
        return tuple(errors)

    def _route_signal_error(self, route: WireRoute, reference: str) -> str | None:
        node_id, separator, port_name = reference.rpartition(".")
        if not separator:
            return f"{route.id}: malformed port reference {reference!r}"
        try:
            node = self.node(node_id)
            port = next(port for port in node.ports if port.name == port_name)
        except (KeyError, StopIteration):
            return f"{route.id}: unknown port {reference!r}"
        if port.signal != route.signal:
            return (
                f"{route.id}: signal {route.signal!r} does not match "
                f"{reference} signal {port.signal!r}"
            )
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_NAME,
            "schema_version": self.version,
            "version": self.version,
            "units": self.units,
            "coordinate_system": self.coordinate_system,
            "case_dimensions": self.case_dimensions.to_dict(),
            "nodes": [node.to_dict() for node in self.nodes],
            "routes": [route.to_dict() for route in self.routes],
            "electronics_backbone": (
                self.electronics_backbone.to_dict()
                if self.electronics_backbone is not None
                else None
            ),
        }

    def to_json(self) -> str:
        """Return deterministic JSON suitable for a manifest artifact."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AssemblyScene:
        if not isinstance(value, Mapping):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("assembly metadata must be an object")
        _validate_structural_value(dict(value), path="assembly")
        if value.get("schema") != SCHEMA_NAME:
            raise ValueError("assembly schema must be 'cadkit.assembly'")
        if value.get("schema_version") != SCHEMA_VERSION or value.get("version") != SCHEMA_VERSION:
            raise ValueError("unsupported or missing assembly schema version")
        if value.get("units") != "mm":
            raise ValueError("assembly units must be 'mm'")
        if value.get("coordinate_system") != "case_local_lower_left_xyz":
            raise ValueError("assembly coordinate system is not canonical")
        if "electronics_backbone" not in value:
            value = {**value, "electronics_backbone": None}
        _require_exact_mapping(
            value,
            frozenset(
                {
                    "schema",
                    "schema_version",
                    "version",
                    "units",
                    "coordinate_system",
                    "case_dimensions",
                    "nodes",
                    "routes",
                    "electronics_backbone",
                }
            ),
            "assembly",
        )
        nodes = value.get("nodes")
        routes = value.get("routes")
        if not isinstance(nodes, list) or not isinstance(routes, list):
            raise ValueError("assembly nodes and routes must be lists")
        node_values = cast(list[Any], nodes)
        route_values = cast(list[Any], routes)
        return cls(
            case_dimensions=Dimensions.from_dict(value["case_dimensions"]),
            nodes=tuple(
                AssemblyNode.from_dict(cast(Mapping[str, Any], node)) for node in node_values
            ),
            routes=tuple(
                WireRoute.from_dict(cast(Mapping[str, Any], route)) for route in route_values
            ),
            version=SCHEMA_VERSION,
            units="mm",
            coordinate_system="case_local_lower_left_xyz",
            electronics_backbone=(
                ElectronicsBackbone.from_dict(value["electronics_backbone"])
                if value.get("electronics_backbone") is not None
                else None
            ),
        )


def _case_dimensions(value: Dimensions | Sequence[float]) -> tuple[float, float, float]:
    if isinstance(value, Dimensions):
        return value.x, value.y, value.z
    if len(value) != 3:
        raise ValueError("case dimensions require length, width, and height")
    return tuple(float(part) for part in value)  # type: ignore[return-value]


def case_local_to_build123d(
    point: Sequence[float],
    case_dimensions: Dimensions | Sequence[float] | None = None,
    *,
    case_length_mm: float | None = None,
    case_width_mm: float | None = None,
) -> Point3:
    """Map a case-local point to Build123d's centred XY origin.

    Z deliberately remains bottom-relative: the existing CAD primitives use
    ``Align.MIN`` in Z.  Thus ``(L/2, W/2, 0)`` maps exactly to ``(0, 0, 0)``.
    """
    if case_dimensions is not None:
        length, width, _ = _case_dimensions(case_dimensions)
    elif case_length_mm is not None and case_width_mm is not None:
        length, width = float(case_length_mm), float(case_width_mm)
    else:
        raise TypeError("provide case_dimensions or case_length_mm and case_width_mm")
    x, y, z = _point3(point)
    return (x - length / 2.0, y - width / 2.0, z)


def build123d_to_case_local(
    point: Sequence[float],
    case_dimensions: Dimensions | Sequence[float],
) -> Point3:
    """Inverse of :func:`case_local_to_build123d`."""
    length, width, _ = _case_dimensions(case_dimensions)
    x, y, z = _point3(point)
    return (x + length / 2.0, y + width / 2.0, z)


def transform_to_build123d(
    transform: Transform, case_dimensions: Dimensions | Sequence[float]
) -> Transform:
    """Adapt a node transform without changing its rotation."""
    return Transform(
        case_local_to_build123d(transform.position, case_dimensions), transform.rotation_deg
    )


def _node(
    node_id: str,
    kind: str,
    position: Point3,
    physical: Dimensions,
    *,
    rotation_deg: Point3 = (0.0, 0.0, 0.0),
    keep_out: Dimensions | None = None,
    parent: str | None = None,
    mobility: str = "fixed",
    provenance: str = "generated-proxy",
    status: str = "provisional",
    layer: int = 0,
    z_order: int = 0,
    visual_ref: str = "",
    proxy_ref: str = "",
    visual: Mapping[str, Any] | None = None,
    ports: tuple[Port, ...] = (),
    metadata: Mapping[str, Any] | None = None,
) -> AssemblyNode:
    return AssemblyNode(
        id=node_id,
        kind=kind,
        transform=Transform(position, rotation_deg),
        physical_dimensions=physical,
        keep_out_dimensions=keep_out,
        parent_id=parent,
        mobility=mobility,
        provenance=provenance,
        status=status,
        layer=layer,
        z_order=z_order,
        visual_ref=visual_ref,
        proxy_ref=proxy_ref,
        visual=visual or {},
        ports=ports,
        metadata=metadata or {},
    )


def _control_node(
    control_id: str,
    role: str,
    component_id: str,
    kind: str,
    gpio: str,
    record: Mapping[str, Any],
    height: float,
    index: int,
) -> AssemblyNode:
    x, y = float(record["x"]), float(record["y"])
    action = control_id in {"action_a", "action_b"}
    cutout_diameter, mounting_land, underside_keepout, terminal_keepout, route_bend = (
        _provisional_control_metrics(component_id)
    )
    # ``size_mm`` remains accepted in source-compatible control records, but it
    # must not create a second aperture value in the manifest/editor.  The
    # component contract is the sole authority for both CAD and preview size.
    diameter = cutout_diameter
    body_diameter = max(14.0, diameter)
    node_z = height - 8.0
    terminal_z = height - mounting_land[1] - underside_keepout[2]
    terminal_local_z = terminal_z - node_z
    return _node(
        f"control.{control_id}",
        kind,
        (x, y, node_z),
        Dimensions(body_diameter, body_diameter, 20.0),
        keep_out=Dimensions(*underside_keepout),
        parent="case.top",
        mobility="constrained_xy",
        provenance="receiving-controlled-unverified",
        status="provisional",
        layer=3,
        z_order=10 + index,
        visual_ref="guuzi-action-2d" if action else "pbs33b-button-2d",
        proxy_ref="guuzi-b09bmrdptn-proxy" if action else "pbs33b-generated-proxy",
        visual=(
            {
                "shape": "rounded-rectangle",
                "width_mm": body_diameter,
                "height_mm": body_diameter,
                "corner_radius_mm": 2.0,
                "diameter_mm": diameter,
            }
            if action
            else {"shape": "circle", "diameter_mm": diameter}
        ),
        ports=(
            Port("signal", (0.0, 0.0, terminal_local_z), role, "output"),
            Port("ground", (0.0, 0.0, terminal_local_z), "GND", "output"),
        ),
        metadata={
            "control_role": role,
            "component_id": component_id,
            "gpio": gpio,
            "polarity": "active-low",
            "cutout_diameter_mm": cutout_diameter,
            "mounting_land_diameter_mm": mounting_land[0],
            "mounting_land_thickness_mm": mounting_land[1],
            "underside_keepout_mm": list(underside_keepout),
            "terminal_keepout_mm": list(terminal_keepout),
            "terminal_route_min_bend_radius_mm": route_bend,
        },
    )


def _control_nodes(records: Sequence[Mapping[str, Any]], height: float) -> list[AssemblyNode]:
    return [
        _control_node(
            control_id,
            role,
            component_id,
            kind,
            gpio,
            record,
            height,
            index,
        )
        for index, (control_id, role, component_id, kind, gpio, record) in enumerate(
            zip(
                CONTROL_IDS,
                CONTROL_ROLES,
                CONTROL_COMPONENT_IDS,
                CONTROL_KINDS,
                CONTROL_GPIOS,
                records,
                strict=True,
            ),
            start=1,
        )
    ]


def _breadboard_preview_ports(preview_y: float, preview_z: float) -> tuple[Port, ...]:
    """Return logical preview contacts without asserting measured placement."""
    # Seller-listed pitch is a topology fact. These right-side contact choices
    # only keep the browser's demo routes clear of control keep-outs; they are
    # not measured placement, insertion, or retention evidence.
    contact_z = preview_z / 2
    signal_x = 12 * ELECTRONICS_BACKBONE_PITCH_MM
    signal_y = tuple(offset * ELECTRONICS_BACKBONE_PITCH_MM for offset in (-5, -3, -1, 1, 3, 5))
    # Scene +Y renders upward in the SVG, so the negative rail is at local -Y.
    ground_y = -preview_y * 0.41
    ground_x = tuple(offset * ELECTRONICS_BACKBONE_PITCH_MM for offset in (8, 9, 16, 15, 14, 7))
    signal_ports = tuple(
        Port(f"signal_{control_id}", (signal_x, y, contact_z), role, "input")
        for control_id, role, y in zip(CONTROL_IDS, CONTROL_ROLES, signal_y, strict=True)
    )
    ground_ports = tuple(
        Port(f"gnd_{control_id}", (x, ground_y, contact_z), "GND")
        for control_id, x in zip(CONTROL_IDS, ground_x, strict=True)
    )
    bridge = Port(
        "gnd_bridge",
        (6 * ELECTRONICS_BACKBONE_PITCH_MM, ground_y, contact_z),
        "GND",
    )
    # The header rows are deliberately separate named contacts.  They make the
    # physical connector-to-breadboard route explicit without claiming that a
    # receiving measurement has frozen the seller-listed breadboard geometry.
    header_contacts = (
        Port("header_12_connector", (9.0, 8.0, contact_z), "GND", "input"),
        Port("header_16_connector", (-9.0, 8.0, contact_z), "GND", "input"),
    )
    return (*signal_ports, *ground_ports, bridge, *header_contacts)


def _control_records(
    controls: Sequence[Mapping[str, Any]] | None,
) -> tuple[Mapping[str, Any], ...]:
    defaults: tuple[Mapping[str, Any], ...] = (
        {"id": "up", "x": 35.0, "y": 60.0, "size_mm": 12.4},
        {"id": "down", "x": 35.0, "y": 20.0, "size_mm": 12.4},
        {"id": "right", "x": 55.0, "y": 40.0, "size_mm": 12.4},
        {"id": "left", "x": 15.0, "y": 40.0, "size_mm": 12.4},
        {"id": "action_a", "x": 105.0, "y": 30.0, "size_mm": 12.4},
        {"id": "action_b", "x": 105.0, "y": 55.0, "size_mm": 12.4},
    )
    records = tuple(controls) if controls is not None else defaults
    if len(records) != len(CONTROL_IDS):
        raise ValueError(f"MIG-02 assembly requires exactly {ACTIVE_CONTROL_COUNT} controls")
    for index, (record, control_id) in enumerate(zip(records, CONTROL_IDS, strict=True)):
        declared_id = record.get("id", control_id)
        if declared_id != control_id:
            raise ValueError(f"control record {index + 1} must declare canonical id {control_id!r}")
        for coordinate in ("x", "y"):
            if coordinate not in record:
                raise ValueError(f"control {control_id!r} is missing {coordinate}")
            _point3((record[coordinate], 0.0, 0.0))
        _dimension_value(record.get("size_mm", 12.0), f"control {control_id} size_mm")
    return records


def _append_electronics_nodes(nodes: list[AssemblyNode]) -> None:
    """Append the provisional electronics and harness nodes in canonical order."""
    preview_x, preview_y, preview_z = ELECTRONICS_BACKBONE_PREVIEW_DIMENSIONS_MM
    nodes.append(
        _node(
            "electronics.breadboard",
            "half_size_breadboard",
            ELECTRONICS_BACKBONE_PREVIEW_POSITION_MM,
            Dimensions(preview_x, preview_y, preview_z),
            rotation_deg=(0.0, 0.0, ELECTRONICS_BREADBOARD_ROTATION_DEG),
            parent="case.bottom",
            provenance="operator-envelope-with-unmeasured-editor-placement",
            status="provisional",
            layer=2,
            z_order=10,
            visual_ref="breadboard-preview-2d",
            proxy_ref="breadboard-preview-3d",
            visual={
                "shape": "half-size-breadboard",
                "width_mm": preview_x,
                "height_mm": preview_y,
                "grid_columns": 30,
                "grid_rows": 10,
                "rail_points": 50,
                "pitch_mm": ELECTRONICS_BACKBONE_PITCH_MM,
            },
            ports=_breadboard_preview_ports(preview_y, preview_z),
            metadata={
                "part": ELECTRONICS_BACKBONE_IDENTITY,
                "supplier_sku": ELECTRONICS_BACKBONE_SUPPLIER_SKU,
                "layout_role": "operator_reported_preview_only",
                "mechanical_interface": False,
                "dimension_status": "operator_reported_preview_not_receiving_evidence",
                "measurement_date": "2026-09-01",
                "measurement_method": "ruler",
                "measurement_scope": "installed_stack",
                "adhesive_included": True,
                "photo_context": "top_side_unscaled",
                "receiving_evidence": False,
                "placement_status": "unmeasured_editor_preview_only",
                "contact_layout_status": "provisional_logical_only",
                "orientation": "long_axis_+X",
                "assembly_rotation_status": "provisional_preview_only",
                "usb_facing": ELECTRONICS_USB_FACING,
                "retention_method": ELECTRONICS_BACKBONE_RETENTION_METHOD,
                "bond_surface": ELECTRONICS_BACKBONE_BOND_SURFACE,
                "product_source_url": ELECTRONICS_BACKBONE_PRODUCT_URL,
            },
        )
    )

    # Feather USB remains at the left perimeter while the breadboard envelope
    # is aligned with case X. Placement is provisional until receiving freeze.
    feather_origin = (30.0, 45.0, 5.6)
    holes = [[2.54, 2.54], [48.26, 2.54], [2.54, 20.32], [48.26, 20.32]]
    feather_visual = {
        "shape": "feather-board",
        "width_mm": 50.8,
        "height_mm": 22.86,
        "mounting_holes_mm": holes,
        "mounting_holes_native_mm": holes,
        "headers": [
            {
                "id": "header_12",
                "x_mm": -13.97,
                "y_mm": -9.0,
                "count": 12,
                "pitch_mm": 2.54,
                "axis": "x",
            },
            {
                "id": "header_16",
                "x_mm": -19.05,
                "y_mm": 9.0,
                "count": 16,
                "pitch_mm": 2.54,
                "axis": "x",
            },
        ],
    }
    control_ports = tuple(
        Port(
            role.lower(),
            (10.0, -20.0 + index, 0.0),
            role,
            "output",
        )
        for index, role in enumerate(CONTROL_ROLES, start=1)
    )
    nodes.append(
        _node(
            "feather",
            "feather_board",
            feather_origin,
            Dimensions(50.8, 22.86, 7.2),
            rotation_deg=(0.0, 0.0, ELECTRONICS_FEATHER_ROTATION_DEG),
            keep_out=Dimensions(57.0, 29.0, 28.7),
            parent="case.bottom",
            provenance="logical-layout-via-breadboard",
            status="provisional",
            layer=2,
            z_order=20,
            visual_ref="feather-board-2d",
            proxy_ref="feather-pid4516-3d",
            visual=feather_visual,
            ports=(
                Port("usb_rear", (27.0, 0.0, 12.15), "USB", "output"),
                Port("usb_left", (27.0, 0.0, 12.15), "USB", "output"),
                Port("gnd", (-10.0, -20.0, 0.0), "GND", "output"),
                *control_ports,
            ),
            metadata={
                "part": "Adafruit Feather nRF52840 Sense PID 4516",
                "orientation": ELECTRONICS_FEATHER_ORIENTATION,
                "rotation_from_previous_deg": ELECTRONICS_ASSEMBLY_ROTATION_DELTA_DEG,
                "rotation_status": "provisional_preview_only",
                "layout_role": "provisional_logical_only",
                "mechanical_interface": False,
            },
        )
    )
    feather_transform = Transform(
        feather_origin,
        (0.0, 0.0, ELECTRONICS_FEATHER_ROTATION_DEG),
    )
    nodes.extend(
        [
            _node(
                "feather.header_12",
                "pid2830_header_row",
                local_to_scene_point(feather_transform, (0.0, -9.0, 8.35)),
                Dimensions(27.94, 2.54, 8.5),
                rotation_deg=(0.0, 0.0, ELECTRONICS_FEATHER_ROTATION_DEG),
                keep_out=Dimensions(30.94, 3.0, 18.5),
                parent="feather",
                provenance="logical-layout-via-breadboard",
                status="provisional",
                layer=4,
                z_order=21,
                visual_ref="header-row-2d",
                proxy_ref="pid2830-12-pin-proxy",
                metadata={
                    "pin_count": 12,
                    "pitch_mm": 2.54,
                    "part": "PID 2830",
                    "layout_role": "provisional_logical_only",
                    "mechanical_interface": False,
                    "connector": "ADA2830_header_12_pin_row",
                    "harness_route_id": "wire.harness.header_12",
                    "min_bend_radius_mm": ELECTRONICS_HARNESS_MIN_BEND_RADIUS_MM,
                    "strain_relief_ref": "harness.header_12.strain_relief",
                    "no_pinch_envelope_ref": "harness.header_12.no_pinch",
                },
                ports=(Port("connector", (0.0, 0.0, 0.0), "HEADER_12", "output"),),
            ),
            _node(
                "feather.header_16",
                "pid2830_header_row",
                local_to_scene_point(feather_transform, (0.0, 9.0, 8.35)),
                Dimensions(38.10, 2.54, 8.5),
                rotation_deg=(0.0, 0.0, ELECTRONICS_FEATHER_ROTATION_DEG),
                keep_out=Dimensions(41.10, 3.0, 18.5),
                parent="feather",
                provenance="logical-layout-via-breadboard",
                status="provisional",
                layer=4,
                z_order=22,
                visual_ref="header-row-2d",
                proxy_ref="pid2830-16-pin-proxy",
                metadata={
                    "pin_count": 16,
                    "pitch_mm": 2.54,
                    "part": "PID 2830",
                    "layout_role": "provisional_logical_only",
                    "mechanical_interface": False,
                    "connector": "ADA2830_header_16_pin_row",
                    "harness_route_id": "wire.harness.header_16",
                    "min_bend_radius_mm": ELECTRONICS_HARNESS_MIN_BEND_RADIUS_MM,
                    "strain_relief_ref": "harness.header_16.strain_relief",
                    "no_pinch_envelope_ref": "harness.header_16.no_pinch",
                },
                ports=(Port("connector", (0.0, 0.0, 0.0), "HEADER_16", "output"),),
            ),
            _node(
                "usb.connector",
                "micro_usb_connector",
                local_to_scene_point(feather_transform, (27.0, 0.0, 12.15)),
                Dimensions(6.0, 14.0, 8.0),
                keep_out=Dimensions(8.0, 16.0, 10.0),
                rotation_deg=(0.0, 0.0, ELECTRONICS_FEATHER_ROTATION_DEG),
                parent="feather",
                provenance="logical-layout-via-breadboard",
                status="provisional",
                layer=4,
                z_order=30,
                visual_ref="usb-slot-2d",
                proxy_ref="micro-usb-proxy",
                visual={
                    "shape": "usb-slot",
                    "width_mm": 14.0,
                    "height_mm": 8.0,
                    "corner_radius_mm": 2.0,
                },
                ports=(Port("cable", (0.0, 3.0, 0.0), "USB", "bidirectional"),),
                metadata={
                    "layout_role": "provisional_logical_only",
                    "mechanical_interface": False,
                    "facing": ELECTRONICS_USB_FACING,
                    "shared_opening_id": "usb.opening",
                    "no_pinch_envelope_ref": "usb.plug_no_pinch",
                },
            ),
            _node(
                "usb.opening",
                "rear_usb_opening",
                local_to_scene_point(feather_transform, (27.0, 0.0, 12.15)),
                Dimensions(6.0, 14.0, 8.0),
                parent="case.shell",
                provenance="logical-layout-via-breadboard",
                status="provisional",
                layer=5,
                z_order=31,
                visual_ref="usb-slot-2d",
                proxy_ref="rear-usb-opening-proxy",
                visual={
                    "shape": "usb-slot",
                    "width_mm": 14.0,
                    "height_mm": 8.0,
                    "corner_radius_mm": 2.0,
                },
                metadata={
                    "layout_role": "provisional_logical_only",
                    "mechanical_interface": False,
                    "facing": ELECTRONICS_USB_FACING,
                    "shared_opening": True,
                    "split_plane_crossing": True,
                    "aligned_to": "usb.connector",
                    "shell_halves": ["case.bottom", "case.top"],
                    "opening_axis": "case_local_-X",
                },
            ),
        ]
    )
    header_specs = (
        (12, -9.0, 27.94, "pid2830-12-pin-connector-proxy"),
        (16, 9.0, 38.10, "pid2830-16-pin-connector-proxy"),
    )
    for pin_count, local_y, row_length, connector_proxy in header_specs:
        header_position = local_to_scene_point(feather_transform, (0.0, local_y, 8.35))
        row_id = f"harness.header_{pin_count}"
        nodes.extend(
            (
                _node(
                    f"{row_id}.connector",
                    "dupont_connector_row",
                    header_position,
                    Dimensions(row_length, 4.0, 8.5),
                    rotation_deg=(0.0, 0.0, ELECTRONICS_FEATHER_ROTATION_DEG),
                    parent=f"feather.header_{pin_count}",
                    provenance="logical-layout-via-breadboard",
                    status="provisional",
                    layer=5,
                    z_order=25 + pin_count,
                    proxy_ref=connector_proxy,
                    metadata={
                        "pin_count": pin_count,
                        "header_row_id": f"feather.header_{pin_count}",
                        "interface": "ADA2830",
                        "layout_role": "provisional_logical_only",
                        "mechanical_interface": False,
                    },
                    ports=(Port("connector", (0.0, 0.0, 0.0), "GND", "output"),),
                ),
                _node(
                    f"{row_id}.strain_relief",
                    "harness_strain_relief",
                    (header_position[0], header_position[1], header_position[2] - 5.0),
                    Dimensions(10.0, 8.0, 6.0),
                    rotation_deg=(0.0, 0.0, ELECTRONICS_FEATHER_ROTATION_DEG),
                    parent=f"{row_id}.connector",
                    provenance="generated-proxy",
                    status="provisional",
                    layer=5,
                    z_order=40 + pin_count,
                    proxy_ref="strain-relief-proxy",
                    metadata={
                        "for_header": f"feather.header_{pin_count}",
                        "protected": True,
                        "layout_role": "provisional_logical_only",
                        "mechanical_interface": False,
                    },
                ),
                _node(
                    f"{row_id}.no_pinch",
                    "harness_no_pinch_envelope",
                    (header_position[0], header_position[1], header_position[2]),
                    Dimensions(row_length + 6.0, 10.0, 18.0),
                    rotation_deg=(0.0, 0.0, ELECTRONICS_FEATHER_ROTATION_DEG),
                    parent=f"{row_id}.connector",
                    provenance="generated-proxy",
                    status="provisional",
                    layer=5,
                    z_order=50 + pin_count,
                    proxy_ref="harness-no-pinch-proxy",
                    metadata={
                        "for_header": f"feather.header_{pin_count}",
                        "protected": True,
                        "envelope_role": "protected_no_pinch",
                        "layout_role": "provisional_logical_only",
                        "mechanical_interface": False,
                    },
                ),
            )
        )


def _node_port_position(node: AssemblyNode, name: str) -> Point3:
    port = next(port for port in node.ports if port.name == name)
    return local_to_scene_point(node.transform, port.position)


def _control_scene_routes(
    nodes: Sequence[AssemblyNode], breadboard_node: AssemblyNode
) -> list[WireRoute]:
    routes: list[WireRoute] = []
    for control_id, role in zip(CONTROL_IDS, CONTROL_ROLES, strict=True):
        control = next(node for node in nodes if node.id == f"control.{control_id}")
        control_route_radius = cast(
            float,
            control.metadata["terminal_route_min_bend_radius_mm"],
        )
        signal_target = _node_port_position(breadboard_node, f"signal_{control_id}")
        signal_start = local_to_scene_point(control.transform, control.ports[0].position)
        routes.append(
            WireRoute(
                f"wire.control_{control_id}",
                f"control.{control_id}.signal",
                f"electronics.breadboard.signal_{control_id}",
                (signal_start, signal_target),
                role,
                provenance="logical-via-breadboard",
                status="logical",
                kind="via-breadboard",
                min_bend_radius_mm=control_route_radius,
                protected=True,
            )
        )
        ground_start = local_to_scene_point(control.transform, control.ports[1].position)
        ground_target = _node_port_position(breadboard_node, f"gnd_{control_id}")
        routes.append(
            WireRoute(
                f"wire.control_ground_{control_id}",
                f"control.{control_id}.ground",
                f"electronics.breadboard.gnd_{control_id}",
                (ground_start, ground_target),
                "GND",
                provenance="logical-via-breadboard",
                status="logical",
                kind="via-breadboard",
                min_bend_radius_mm=control_route_radius,
                protected=True,
            )
        )
    return routes


def _header_scene_routes(
    nodes: Sequence[AssemblyNode], breadboard_node: AssemblyNode
) -> list[WireRoute]:
    routes: list[WireRoute] = []
    for pin_count in (12, 16):
        connector_node = next(
            node for node in nodes if node.id == f"harness.header_{pin_count}.connector"
        )
        connector_position = _node_port_position(connector_node, "connector")
        breadboard_position = _node_port_position(breadboard_node, f"header_{pin_count}_connector")
        midpoint = (
            (connector_position[0] + breadboard_position[0]) / 2.0,
            connector_position[1] + 8.0,
            connector_position[2],
        )
        routes.append(
            WireRoute(
                f"wire.harness.header_{pin_count}",
                f"harness.header_{pin_count}.connector.connector",
                f"electronics.breadboard.header_{pin_count}_connector",
                (connector_position, midpoint, breadboard_position),
                "GND",
                provenance="logical-via-breadboard-harness",
                status="logical",
                kind="dupont-header-harness",
                min_bend_radius_mm=ELECTRONICS_HARNESS_MIN_BEND_RADIUS_MM,
                protected=True,
                strain_relief_ref=f"harness.header_{pin_count}.strain_relief",
                no_pinch_envelope_ref=f"harness.header_{pin_count}.no_pinch",
            )
        )
    return routes


def _ground_bridge_route(feather_node: AssemblyNode, breadboard_node: AssemblyNode) -> WireRoute:
    source = _node_port_position(feather_node, "gnd")
    target = _node_port_position(breadboard_node, "gnd_bridge")
    return WireRoute(
        ELECTRONICS_BACKBONE_GROUND_BRIDGE_ROUTE_ID,
        "feather.gnd",
        "electronics.breadboard.gnd_bridge",
        (
            source,
            (target[0], source[1], source[2]),
            target,
        ),
        "GND",
        provenance="logical-via-breadboard",
        status="logical",
        kind="via-breadboard",
        min_bend_radius_mm=ELECTRONICS_HARNESS_MIN_BEND_RADIUS_MM,
        protected=True,
    )


def _electronics_scene_routes(nodes: Sequence[AssemblyNode]) -> tuple[WireRoute, ...]:
    """Build the canonical control, header, and ground-bridge routes."""
    feather_node = next(node for node in nodes if node.id == "feather")
    breadboard_node = next(node for node in nodes if node.id == "electronics.breadboard")
    routes = _control_scene_routes(nodes, breadboard_node)
    routes.extend(_header_scene_routes(nodes, breadboard_node))
    routes.append(_ground_bridge_route(feather_node, breadboard_node))
    return tuple(routes)


def build_demo_assembly(
    case_dimensions: Dimensions | Sequence[float] = (130.0, 90.0, 65.0),
    *,
    buttons: Sequence[Mapping[str, Any]] | None = None,
    case_fillet_radius_mm: float = 4.0,
) -> AssemblyScene:
    """Build the active MIG-02 six-control teaching assembly.

    ``buttons`` is retained as the source-compatible argument name used by
    the case worker.  Its records are ordered ``up``, ``down``, ``right``,
    ``left``, ``action_a``, ``action_b`` and must carry those canonical IDs
    when an ``id`` is supplied.  The scene itself always follows the
    exact-six contract.
    """
    length, width, height = _case_dimensions(case_dimensions)
    fillet_radius = _dimension_value(case_fillet_radius_mm, "case_fillet_radius_mm")
    case = Dimensions(length, width, height)
    records = _control_records(buttons)
    split_z = min(height / 2.0, AUTHORITATIVE_SNAP_GEOMETRY.max_lower_shell_height_mm)
    nodes: list[AssemblyNode] = [
        _node(
            "case.shell",
            "case_shell",
            (length / 2, width / 2, height / 2),
            case,
            visual_ref="case-shell-2d",
            proxy_ref="case-shell-3d-proxy",
            visual={
                "shape": "rounded-rectangle",
                "width_mm": length,
                "height_mm": width,
                "corner_radius_mm": fillet_radius,
            },
        ),
        _node(
            "case.bottom",
            "case_bottom",
            (length / 2, width / 2, split_z / 2),
            Dimensions(length, width, split_z),
            parent="case.shell",
            mobility="derived",
            provenance="derived",
            layer=1,
            z_order=1,
            visual_ref="case-bottom-2d",
            proxy_ref="case-bottom-3d-proxy",
            visual={
                "shape": "rounded-rectangle",
                "width_mm": length,
                "height_mm": width,
                "corner_radius_mm": fillet_radius,
            },
        ),
        _node(
            "case.top",
            "case_top",
            (length / 2, width / 2, split_z + (height - split_z) / 2),
            Dimensions(length, width, height - split_z),
            parent="case.shell",
            mobility="derived",
            provenance="derived",
            layer=2,
            z_order=2,
            visual_ref="case-top-2d",
            proxy_ref="case-top-3d-proxy",
            visual={
                "shape": "rounded-rectangle",
                "width_mm": length,
                "height_mm": width,
                "corner_radius_mm": fillet_radius,
            },
        ),
    ]
    nodes.extend(_control_nodes(records, height))
    _append_electronics_nodes(nodes)
    routes = _electronics_scene_routes(nodes)
    return AssemblyScene(
        case,
        tuple(nodes),
        tuple(routes),
        electronics_backbone=ElectronicsBackbone(),
    )


def _electronics_stack_intrusion_mm(scene: AssemblyScene) -> float:
    """Use signed stack height for production and preview height otherwise."""
    backbone = scene.electronics_backbone
    if backbone is not None and backbone.is_frozen_for_export:
        return cast(float, backbone.stack_height_mm)
    return ELECTRONICS_BACKBONE_PREVIEW_DIMENSIONS_MM[2]


def placements_from_assembly(scene: AssemblyScene) -> Any:
    """Build the worker placement artifact from the active six-control scene.

    The ``control_*`` fields are the shared CAD/editor interface.  Their
    order is deliberately copied from the scene, not inferred from display
    labels. This adapter does not transfer the backbone's provisional envelope,
    editor placement, or contact layout into production constraint geometry.
    """
    from .aabb import AABB, Route, Volume
    from .constraints import Placements
    from .parametric import SNAP_FIT_TOLERANCE_MM
    from .snap_geometry import AUTHORITATIVE_SNAP_GEOMETRY

    shell = scene.node("case.shell")
    controls = tuple(scene.node(f"control.{control_id}") for control_id in CONTROL_IDS)
    case_dimensions = shell.physical_dimensions

    def box(
        name: str,
        node: AssemblyNode,
        dimensions: Dimensions,
        *,
        keep_out: bool,
        cable_corridor: bool = False,
    ) -> Volume:
        x, y, z = node.transform.position
        angle = math.radians(node.transform.rotation_deg[2])
        rotated_x = abs(dimensions.x * math.cos(angle)) + abs(dimensions.y * math.sin(angle))
        rotated_y = abs(dimensions.x * math.sin(angle)) + abs(dimensions.y * math.cos(angle))
        return Volume(
            name,
            AABB(
                x - rotated_x / 2,
                y - rotated_y / 2,
                z - dimensions.z / 2,
                x + rotated_x / 2,
                y + rotated_y / 2,
                z + dimensions.z / 2,
            ),
            is_keep_out=keep_out,
            is_cable_corridor=cable_corridor,
        )

    positions_xy = tuple(node.transform.position[:2] for node in controls)
    component_metrics = tuple(
        _provisional_control_metrics(cast(str, node.metadata["component_id"])) for node in controls
    )
    cutout_diameters = tuple(metric[0] for metric in component_metrics)
    mounting_land_diameters = tuple(metric[1][0] for metric in component_metrics)
    mounting_land_thicknesses = tuple(metric[1][1] for metric in component_metrics)
    underside_keepouts = tuple(metric[2] for metric in component_metrics)
    terminal_keepouts = tuple(metric[3] for metric in component_metrics)
    route_min_bend_radii = tuple(metric[4] for metric in component_metrics)
    volumes: list[Volume] = []
    for control, cutout_diameter, mounting_land_thickness, underside_keepout in zip(
        controls,
        cutout_diameters,
        mounting_land_thicknesses,
        underside_keepouts,
        strict=True,
    ):
        x, y = control.transform.position[:2]
        panel_inner_z = case_dimensions.z - mounting_land_thickness
        volumes.extend(
            (
                Volume(
                    f"{control.id}.keepout",
                    AABB(
                        x - underside_keepout[0] / 2,
                        y - underside_keepout[1] / 2,
                        panel_inner_z - underside_keepout[2],
                        x + underside_keepout[0] / 2,
                        y + underside_keepout[1] / 2,
                        panel_inner_z,
                    ),
                ),
                Volume(
                    f"{control.id}.cutout",
                    AABB(
                        x - cutout_diameter / 2,
                        y - cutout_diameter / 2,
                        panel_inner_z,
                        x + cutout_diameter / 2,
                        y + cutout_diameter / 2,
                        case_dimensions.z,
                    ),
                    is_keep_out=False,
                ),
            )
        )
    # The Feather and USB nodes are provisional logical previews only. Their
    # placement depends on the still-unmeasured breadboard stack, so the active
    # placement artifact must not emit the historical direct-Feather cavity or
    # USB opening as production constraint geometry.
    snap = AssemblyNode(
        "snap_receiver",
        "generated_snap_receiver",
        Transform((case_dimensions.x * 0.25, case_dimensions.y * 0.20, case_dimensions.z / 2)),
        Dimensions(
            AUTHORITATIVE_SNAP_GEOMETRY.clip_length_mm + 2 * SNAP_FIT_TOLERANCE_MM,
            AUTHORITATIVE_SNAP_GEOMETRY.clip_depth_mm + 2 * SNAP_FIT_TOLERANCE_MM,
            AUTHORITATIVE_SNAP_GEOMETRY.clip_height_mm + SNAP_FIT_TOLERANCE_MM,
        ),
        None,
        None,
        "derived",
        "generated-parametric",
        "provisional",
        0,
        0,
        "",
        "",
    )
    volumes.append(box("snap_receiver", snap, snap.physical_dimensions, keep_out=True))

    # Preview-only physical envelopes make the harness contract visible to the
    # editor and the worker.  They are not manufacturing geometry or evidence.
    usb = scene.node("usb.connector")
    if usb.keep_out_dimensions is not None:
        volumes.append(box("usb.plug_no_pinch", usb, usb.keep_out_dimensions, keep_out=True))
    for pin_count in (12, 16):
        for suffix, keep_out, cable_corridor in (
            ("connector", False, True),
            ("strain_relief", False, True),
            ("no_pinch", True, False),
        ):
            node = scene.node(f"harness.header_{pin_count}.{suffix}")
            volumes.append(
                box(
                    node.id,
                    node,
                    node.physical_dimensions,
                    keep_out=keep_out,
                    cable_corridor=cable_corridor,
                )
            )

    # The clear-Z envelope starts above the installed-stack intrusion and ends
    # at the available inside-top boundary, capped at the unchanged 60 mm
    # production requirement. Short previews therefore display the space that
    # actually exists and fail the production gate instead of projecting the
    # required envelope outside the shell.
    stack_intrusion_mm = _electronics_stack_intrusion_mm(scene)
    clear_min_z = ELECTRONICS_SHELL_WALL_MM + stack_intrusion_mm + ELECTRONICS_SNAP_ADDITIONAL_MM
    inside_top_z = max(
        clear_min_z,
        scene.case_dimensions.z - ELECTRONICS_SHELL_WALL_MM - ELECTRONICS_SNAP_ADDITIONAL_MM,
    )
    clear_max_z = min(
        clear_min_z + ELECTRONICS_MIN_INTERNAL_CLEAR_Z_MM,
        inside_top_z,
    )
    volumes.append(
        Volume(
            "electronics.internal_clear_z",
            AABB(
                ELECTRONICS_SHELL_WALL_MM + 1.0,
                ELECTRONICS_SHELL_WALL_MM + 22.0,
                clear_min_z,
                53.0,
                56.0,
                clear_max_z,
            ),
            is_keep_out=False,
            is_cable_corridor=True,
        )
    )

    # Keep every semantic signal and common-ground path as a structured route.
    # Route names remain stable across moves while their attached endpoints are
    # regenerated from the live assembly scene.
    routes = tuple(
        Route(
            route.id,
            scene.route_waypoints(route),
            route.min_bend_radius_mm,
        )
        for route in scene.routes
    )
    return Placements(
        case_length_mm=case_dimensions.x,
        case_width_mm=case_dimensions.y,
        case_thickness_mm=case_dimensions.z,
        control_ids=CONTROL_IDS,
        control_roles=CONTROL_ROLES,
        control_component_ids=CONTROL_COMPONENT_IDS,
        control_centres_xy=positions_xy,
        control_cutout_diameters_mm=cutout_diameters,
        control_mounting_land_diameters_mm=mounting_land_diameters,
        control_mounting_land_thicknesses_mm=mounting_land_thicknesses,
        control_underside_keepouts_mm=underside_keepouts,
        control_terminal_keepouts_mm=terminal_keepouts,
        control_terminal_route_min_bend_radii_mm=route_min_bend_radii,
        control_geometry_records=(),
        volumes=tuple(volumes),
        routes=routes,
        electronics_backbone=scene.electronics_backbone,
    )


__all__ = [
    "CONTROL_COMPONENT_IDS",
    "CONTROL_GPIOS",
    "CONTROL_IDS",
    "CONTROL_KINDS",
    "CONTROL_ROLES",
    "ELECTRONICS_ASSEMBLY_ROTATION_DELTA_DEG",
    "ELECTRONICS_BACKBONE_BOND_SURFACE",
    "ELECTRONICS_BACKBONE_FEATHER_CONNECTION",
    "ELECTRONICS_BACKBONE_HEADER_PIN_COUNTS",
    "ELECTRONICS_BACKBONE_IDENTITY",
    "ELECTRONICS_BACKBONE_PITCH_MM",
    "ELECTRONICS_BACKBONE_PREVIEW_BASIS_URL",
    "ELECTRONICS_BACKBONE_PREVIEW_DIMENSIONS_MM",
    "ELECTRONICS_BACKBONE_PREVIEW_POSITION_MM",
    "ELECTRONICS_BACKBONE_PRODUCT_URL",
    "ELECTRONICS_BACKBONE_RETENTION_METHOD",
    "ELECTRONICS_BACKBONE_STATUS_FROZEN",
    "ELECTRONICS_BACKBONE_STATUS_UNMEASURED",
    "ELECTRONICS_BACKBONE_SUPPLIER_SKU",
    "ELECTRONICS_BACKBONE_TOPOLOGY_GRID",
    "ELECTRONICS_BACKBONE_TOPOLOGY_POINTS",
    "ELECTRONICS_BACKBONE_TOPOLOGY_RAIL_POINTS",
    "ELECTRONICS_BREADBOARD_ROTATION_DEG",
    "ELECTRONICS_FEATHER_ORIENTATION",
    "ELECTRONICS_FEATHER_ROTATION_DEG",
    "ELECTRONICS_HARNESS_MIN_BEND_RADIUS_MM",
    "ELECTRONICS_MIN_INTERNAL_CLEAR_Z_MM",
    "ELECTRONICS_SHELL_WALL_MM",
    "ELECTRONICS_SNAP_ADDITIONAL_MM",
    "ELECTRONICS_USB_FACING",
    "MAX_ASSEMBLY_BYTES",
    "MAX_ASSEMBLY_DEPTH",
    "MAX_ASSEMBLY_LIST_ITEMS",
    "MAX_ASSEMBLY_NODES",
    "MAX_ASSEMBLY_NUMBER_ABS",
    "MAX_ASSEMBLY_OBJECT_KEYS",
    "MAX_ASSEMBLY_PORTS_PER_NODE",
    "MAX_ASSEMBLY_ROUTES",
    "MAX_ASSEMBLY_STRING",
    "MAX_ASSEMBLY_WAYPOINTS",
    "MAX_CASE_DIMENSION_MM",
    "MAX_COMPONENT_DIMENSION_MM",
    "MAX_FIELD_COUNT",
    "MAX_HEADER_PINS",
    "MAX_HEADER_ROWS",
    "MAX_MOUNTING_HOLES",
    "MAX_POSITION_MM",
    "MAX_ROTATION_DEG",
    "SCHEMA_NAME",
    "SCHEMA_VERSION",
    "AssemblyNode",
    "AssemblyScene",
    "Dimensions",
    "ElectronicsBackbone",
    "ElectronicsTopology",
    "Port",
    "Transform",
    "WireRoute",
    "build123d_to_case_local",
    "build_demo_assembly",
    "case_local_to_build123d",
    "local_to_scene_point",
    "placements_from_assembly",
    "transform_to_build123d",
]
