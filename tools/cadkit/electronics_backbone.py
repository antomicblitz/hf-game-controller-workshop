"""Strict loader for the trusted MIG-03 electronics-backbone record.

The assembly placement contains only the values needed by the CAD worker.  It
is not evidence: a case can author any JSON value there.  This module is the
production boundary that reads a real ``FrozenEvidenceRef``, validates the
complete receiving-controlled block, and derives the existing
``ElectronicsBackbone`` value with the reference SHA as its binding.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .assembly import (
    ELECTRONICS_BACKBONE_BOND_SURFACE,
    ELECTRONICS_BACKBONE_FEATHER_CONNECTION,
    ELECTRONICS_BACKBONE_HEADER_PIN_COUNTS,
    ELECTRONICS_BACKBONE_IDENTITY,
    ELECTRONICS_BACKBONE_PITCH_MM,
    ELECTRONICS_BACKBONE_RETENTION_METHOD,
    ELECTRONICS_BACKBONE_STATUS_FROZEN,
    ELECTRONICS_BACKBONE_SUPPLIER_SKU,
    ELECTRONICS_BACKBONE_TOPOLOGY_GRID,
    ELECTRONICS_BACKBONE_TOPOLOGY_POINTS,
    ELECTRONICS_BACKBONE_TOPOLOGY_RAIL_POINTS,
    ELECTRONICS_FEATHER_ORIENTATION,
    MAX_ASSEMBLY_NUMBER_ABS,
    MAX_COMPONENT_DIMENSION_MM,
    MAX_FIELD_COUNT,
    MAX_POSITION_MM,
    ElectronicsBackbone,
    ElectronicsTopology,
    Point3,
)
from .evidence import FrozenEvidenceRef, load_snapshot

ELECTRONICS_BACKBONE_GATE = "electronics_backbone"
ELECTRONICS_BACKBONE_COORDINATE_FRAME = "breadboard_local_xyz"
ELECTRONICS_BACKBONE_GRID_FRAME = "breadboard_local_grid"
ELECTRONICS_BACKBONE_FEATHER_PID = "4516"
ELECTRONICS_BACKBONE_BREADBOARD_PRODUCT_URL = (
    "https://thepihut.com/products/raspberry-pi-breadboard-half-size?variant=758602977"
)
ELECTRONICS_BACKBONE_HEADER_PRODUCT_URL = (
    "https://shop.pimoroni.com/products/feather-stacking-headers-12-pin-and-16-pin"
    "-female-headers?variant=13709873863"
)
ELECTRONICS_BACKBONE_MIN_EXPECTED_COUNT = 7
ELECTRONICS_BACKBONE_GRID_COLUMNS = 30
ELECTRONICS_BACKBONE_GRID_ROWS = 10

_IDENTITY_KEYS = frozenset(
    {
        "breadboard_name",
        "breadboard_supplier_sku",
        "breadboard_product_url",
        "feather_pid",
        "header_id",
        "header_product_url",
        "header_pin_counts",
    }
)
_RECEIVED_IDENTITY_KEYS = frozenset(
    {"breadboard_name", "breadboard_supplier_sku", "feather_pid", "header_id", "header_pin_counts"}
)
_TOPOLOGY_KEYS = frozenset(
    {"points", "rail_point_counts", "grid", "pitch_mm", "self_adhesive_rear"}
)
_GRID_PLACEMENT_KEYS = frozenset({"coordinate_frame", "column", "row"})
_RAIL_CONTINUITY_KEYS = frozenset({"rail_1", "rail_2"})
_BACKBONE_KEYS = frozenset(
    {
        "intended_identity",
        "seller_topology",
        "received_identity",
        "received_count",
        "expected_count",
        "outside_dimensions_mm",
        "underside_base_condition",
        "adhesive_condition",
        "assembled_stack_height_mm",
        "feather_grid_placement",
        "feather_orientation",
        "coordinate_frame",
        "placement_offset_mm",
        "usb_offset_mm",
        "header_insertion_mm",
        "header_protrusion_mm",
        "usb_plug_clearance_mm",
        "retention_method",
        "adhesive_bond_interface",
        "final_placement_result",
        "adhesive_bond_result",
        "usb_clearance_result",
        "interference_result",
        "rail_continuity",
        "six_signal_continuity",
        "termination_result",
        "coupon_binding",
        "signed_receiving_row",
        "evidence_ids",
    }
)
_SIGNALS = ("up", "down", "right", "left", "action_a", "action_b")
_MAX_TEXT = 256
_MAX_EVIDENCE_IDS = 64
_ADHESIVE_BOND_INTERFACE_KEYS = frozenset({"surface"})


class ElectronicsBackboneEvidenceError(ValueError):
    """The trusted MIG-03 backbone record is malformed or mismatched."""


@dataclass(frozen=True)
class ElectronicsBackboneEvidence:
    """Validated receiving observations and their immutable evidence ref."""

    ref: FrozenEvidenceRef
    received_identity: Mapping[str, Any]
    received_count: int
    expected_count: int
    outside_dimensions_mm: tuple[float, float, float]
    underside_base_condition: str
    adhesive_condition: str
    assembled_stack_height_mm: float
    feather_grid_placement: Mapping[str, Any]
    feather_orientation: str
    coordinate_frame: str
    placement_offset_mm: Point3
    usb_offset_mm: Point3
    header_insertion_mm: float
    header_protrusion_mm: float
    usb_plug_clearance_mm: float
    retention_method: str
    adhesive_bond_interface: Mapping[str, Any]
    final_placement_result: bool
    adhesive_bond_result: bool
    usb_clearance_result: bool
    interference_result: bool
    rail_continuity: Mapping[str, bool]
    six_signal_continuity: Mapping[str, bool]
    termination_result: bool
    coupon_binding: str
    signed_receiving_row: str
    evidence_ids: tuple[str, ...]

    @classmethod
    def from_frozen_evidence(
        cls, evidence: FrozenEvidenceRef | Path | str
    ) -> ElectronicsBackboneEvidence:
        """Load and validate one real, frozen electronics-backbone record."""
        ref = (
            evidence
            if isinstance(evidence, FrozenEvidenceRef)
            else load_snapshot(evidence, gate_name=ELECTRONICS_BACKBONE_GATE)
        )
        if ref.gate_name != ELECTRONICS_BACKBONE_GATE:
            raise ElectronicsBackboneEvidenceError(
                "electronics backbone evidence must be bound to the electronics_backbone gate"
            )
        ref.revalidate()
        _validate_path_integrity(ref)
        content = ref.content
        computed = _mapping(content.get("computed"), "computed")
        block = _mapping(computed.get("electronics_backbone"), "computed.electronics_backbone")
        _require_exact_keys(block, _BACKBONE_KEYS, "computed.electronics_backbone")
        _validate_intended_identity(block["intended_identity"])
        _validate_topology(block["seller_topology"])

        received_identity = _mapping(block["received_identity"], "received_identity")
        _require_exact_keys(received_identity, _RECEIVED_IDENTITY_KEYS, "received_identity")
        _validate_received_identity(received_identity)
        received_count = _positive_int(block["received_count"], "received_count")
        expected_count = _positive_int(block["expected_count"], "expected_count")
        if expected_count < ELECTRONICS_BACKBONE_MIN_EXPECTED_COUNT:
            raise ElectronicsBackboneEvidenceError(
                "expected_count must be at least 7 for the approved 5+2 backbone allocation"
            )
        if received_count < expected_count:
            raise ElectronicsBackboneEvidenceError(
                "received_count must be at least expected_count for a frozen backbone record"
            )

        outside_dimensions = _positive_dimensions(
            block["outside_dimensions_mm"], "outside_dimensions_mm"
        )
        stack_height = _positive_number(
            block["assembled_stack_height_mm"], "assembled_stack_height_mm"
        )
        grid_placement = _mapping(block["feather_grid_placement"], "feather_grid_placement")
        _require_exact_keys(grid_placement, _GRID_PLACEMENT_KEYS, "feather_grid_placement")
        if grid_placement.get("coordinate_frame") != ELECTRONICS_BACKBONE_GRID_FRAME:
            raise ElectronicsBackboneEvidenceError(
                "feather_grid_placement.coordinate_frame must be breadboard_local_grid"
            )
        _bounded_grid_index(
            grid_placement["column"],
            "feather_grid_placement.column",
            limit=ELECTRONICS_BACKBONE_GRID_COLUMNS,
        )
        _bounded_grid_index(
            grid_placement["row"],
            "feather_grid_placement.row",
            limit=ELECTRONICS_BACKBONE_GRID_ROWS,
        )
        feather_orientation = _text(block["feather_orientation"], "feather_orientation")
        if feather_orientation != ELECTRONICS_FEATHER_ORIENTATION:
            raise ElectronicsBackboneEvidenceError(
                "feather_orientation must match the active case-local USB-left orientation"
            )
        coordinate_frame = _text(block["coordinate_frame"], "coordinate_frame")
        if coordinate_frame != ELECTRONICS_BACKBONE_COORDINATE_FRAME:
            raise ElectronicsBackboneEvidenceError(
                "electronics backbone offsets must use breadboard_local_xyz"
            )
        placement_offset = _finite_point(block["placement_offset_mm"], "placement_offset_mm")
        usb_offset = _finite_point(block["usb_offset_mm"], "usb_offset_mm")
        insertion = _positive_number(block["header_insertion_mm"], "header_insertion_mm")
        protrusion = _positive_number(block["header_protrusion_mm"], "header_protrusion_mm")
        usb_clearance = _positive_number(block["usb_plug_clearance_mm"], "usb_plug_clearance_mm")

        retention_method = _text(block["retention_method"], "retention_method")
        if retention_method != ELECTRONICS_BACKBONE_RETENTION_METHOD:
            raise ElectronicsBackboneEvidenceError(
                "electronics backbone retention_method does not match the active contract"
            )
        adhesive_bond_interface = _validate_adhesive_bond_interface(
            block["adhesive_bond_interface"], "adhesive_bond_interface"
        )
        results = {
            field: _passed_boolean(block[field], field)
            for field in (
                "final_placement_result",
                "adhesive_bond_result",
                "usb_clearance_result",
                "interference_result",
                "termination_result",
            )
        }
        rail_continuity = _mapping(block["rail_continuity"], "rail_continuity")
        _require_exact_keys(rail_continuity, _RAIL_CONTINUITY_KEYS, "rail_continuity")
        rail_results = {
            rail: _passed_boolean(rail_continuity[rail], f"rail_continuity.{rail}")
            for rail in ("rail_1", "rail_2")
        }
        signals = _mapping(block["six_signal_continuity"], "six_signal_continuity")
        _require_exact_keys(signals, frozenset(_SIGNALS), "six_signal_continuity")
        signal_results = {
            signal: _passed_boolean(signals[signal], f"six_signal_continuity.{signal}")
            for signal in _SIGNALS
        }
        evidence_ids = _evidence_ids(block["evidence_ids"])
        coupon_binding = _text(block["coupon_binding"], "coupon_binding")
        if coupon_binding not in evidence_ids:
            raise ElectronicsBackboneEvidenceError(
                "coupon_binding must identify one of evidence_ids"
            )
        signed_row = _text(block["signed_receiving_row"], "signed_receiving_row")
        return cls(
            ref=ref,
            received_identity=dict(received_identity),
            received_count=received_count,
            expected_count=expected_count,
            outside_dimensions_mm=outside_dimensions,
            underside_base_condition=_text(
                block["underside_base_condition"], "underside_base_condition"
            ),
            adhesive_condition=_text(block["adhesive_condition"], "adhesive_condition"),
            assembled_stack_height_mm=stack_height,
            feather_grid_placement=dict(grid_placement),
            feather_orientation=feather_orientation,
            coordinate_frame=coordinate_frame,
            placement_offset_mm=placement_offset,
            usb_offset_mm=usb_offset,
            header_insertion_mm=insertion,
            header_protrusion_mm=protrusion,
            usb_plug_clearance_mm=usb_clearance,
            retention_method=retention_method,
            adhesive_bond_interface=adhesive_bond_interface,
            final_placement_result=results["final_placement_result"],
            adhesive_bond_result=results["adhesive_bond_result"],
            usb_clearance_result=results["usb_clearance_result"],
            interference_result=results["interference_result"],
            rail_continuity=rail_results,
            six_signal_continuity=signal_results,
            termination_result=results["termination_result"],
            coupon_binding=coupon_binding,
            signed_receiving_row=signed_row,
            evidence_ids=evidence_ids,
        )

    def to_electronics_backbone(self) -> ElectronicsBackbone:
        """Convert this validated record to the existing placement value."""
        return ElectronicsBackbone(
            identity=ELECTRONICS_BACKBONE_IDENTITY,
            supplier_sku=ELECTRONICS_BACKBONE_SUPPLIER_SKU,
            feather_connection=ELECTRONICS_BACKBONE_FEATHER_CONNECTION,
            header_pin_counts=ELECTRONICS_BACKBONE_HEADER_PIN_COUNTS,
            topology=ElectronicsTopology(
                points=ELECTRONICS_BACKBONE_TOPOLOGY_POINTS,
                rail_point_counts=ELECTRONICS_BACKBONE_TOPOLOGY_RAIL_POINTS,
                grid=ELECTRONICS_BACKBONE_TOPOLOGY_GRID,
                pitch_mm=ELECTRONICS_BACKBONE_PITCH_MM,
                self_adhesive_rear=True,
            ),
            status=ELECTRONICS_BACKBONE_STATUS_FROZEN,
            retention_method=self.retention_method,
            outside_dimensions_mm=self.outside_dimensions_mm,
            stack_height_mm=self.assembled_stack_height_mm,
            placement_offset_mm=self.placement_offset_mm,
            usb_offset_mm=self.usb_offset_mm,
            insertion_depth_mm=self.header_insertion_mm,
            adhesive_bond_interface=dict(self.adhesive_bond_interface),
            adhesive_bond_verified=self.adhesive_bond_result,
            signed_record_binding=self.ref.sha256,
        )

    @property
    def backbone(self) -> ElectronicsBackbone:
        """Return the validated value used by sandbox placements."""
        return self.to_electronics_backbone()


def load_electronics_backbone(
    evidence: FrozenEvidenceRef | Path | str,
) -> ElectronicsBackboneEvidence:
    """Convenience wrapper for the production electronics-backbone boundary."""
    return ElectronicsBackboneEvidence.from_frozen_evidence(evidence)


def _validate_path_integrity(ref: FrozenEvidenceRef) -> None:
    if ref.path.is_symlink() or not ref.path.is_file() or ref.path.suffix != ".json":
        raise ElectronicsBackboneEvidenceError(
            f"electronics backbone evidence path is not a regular file: {ref.path}"
        )
    if (
        not isinstance(ref.content.get("id"), str)
        or ref.content.get("id") != ref.record_id
        or not ref.record_id
        or ref.record_id != ref.path.stem
    ):
        raise ElectronicsBackboneEvidenceError(
            "electronics backbone evidence id must match the JSON filename stem"
        )
    if ref.sha256 != ref.sha256.lower() or len(ref.sha256) != 64:
        raise ElectronicsBackboneEvidenceError("electronics backbone evidence SHA is malformed")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ElectronicsBackboneEvidenceError(f"{label} must be an object")
    result = cast(Mapping[object, Any], value)
    if any(not isinstance(key, str) for key in result):
        raise ElectronicsBackboneEvidenceError(f"{label} object keys must be strings")
    return cast(Mapping[str, Any], result)


def _require_exact_keys(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    if set(value) != set(expected):
        raise ElectronicsBackboneEvidenceError(f"{label} has unknown or missing fields")


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_TEXT:
        raise ElectronicsBackboneEvidenceError(f"{label} must be a non-empty string")
    return value


def _positive_int(value: Any, label: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > MAX_FIELD_COUNT
    ):
        raise ElectronicsBackboneEvidenceError(f"{label} must be a bounded positive integer")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ElectronicsBackboneEvidenceError(f"{label} must be a non-negative integer")
    return value


def _bounded_grid_index(value: Any, label: str, *, limit: int) -> int:
    result = _nonnegative_int(value, label)
    if result >= limit:
        raise ElectronicsBackboneEvidenceError(
            f"{label} must be within the 0-based grid range [0, {limit})"
        )
    return result


def _positive_number(
    value: Any, label: str, *, maximum: float = MAX_COMPONENT_DIMENSION_MM
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ElectronicsBackboneEvidenceError(f"{label} must be a finite positive number")
    result = float(value)
    if not math.isfinite(result) or result <= 0 or result > maximum:
        raise ElectronicsBackboneEvidenceError(f"{label} must be a bounded finite positive number")
    return result


def _positive_dimensions(value: Any, label: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)):
        raise ElectronicsBackboneEvidenceError(f"{label} must contain three dimensions")
    values = cast(Sequence[Any], value)
    if len(values) != 3:
        raise ElectronicsBackboneEvidenceError(f"{label} must contain three dimensions")
    result = tuple(_positive_number(item, f"{label}[{index}]") for index, item in enumerate(values))
    return cast(tuple[float, float, float], result)


def _finite_point(value: Any, label: str) -> Point3:
    if not isinstance(value, (list, tuple)):
        raise ElectronicsBackboneEvidenceError(f"{label} must contain three finite coordinates")
    values = cast(Sequence[Any], value)
    if len(values) != 3:
        raise ElectronicsBackboneEvidenceError(f"{label} must contain three finite coordinates")
    result: tuple[float, ...] = tuple(
        _finite_number(item, f"{label}[{index}]", maximum=MAX_POSITION_MM)
        for index, item in enumerate(values)
    )
    return cast(Point3, result)


def _finite_number(value: Any, label: str, *, maximum: float = MAX_ASSEMBLY_NUMBER_ABS) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ElectronicsBackboneEvidenceError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or abs(result) > maximum:
        raise ElectronicsBackboneEvidenceError(f"{label} must be a bounded finite number")
    return result


def _passed_boolean(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise ElectronicsBackboneEvidenceError(f"{label} must be a boolean")
    if not value:
        raise ElectronicsBackboneEvidenceError(f"{label} must be true for production")
    return value


def _validate_adhesive_bond_interface(value: Any, label: str) -> Mapping[str, Any]:
    interface = _mapping(value, label)
    _require_exact_keys(interface, _ADHESIVE_BOND_INTERFACE_KEYS, label)
    if interface["surface"] != ELECTRONICS_BACKBONE_BOND_SURFACE:
        raise ElectronicsBackboneEvidenceError(
            f"{label}.surface must be {ELECTRONICS_BACKBONE_BOND_SURFACE}"
        )
    return dict(interface)


def _validate_intended_identity(value: Any) -> None:
    identity = _mapping(value, "intended_identity")
    _require_exact_keys(identity, _IDENTITY_KEYS, "intended_identity")
    expected = {
        "breadboard_name": ELECTRONICS_BACKBONE_IDENTITY,
        "breadboard_supplier_sku": ELECTRONICS_BACKBONE_SUPPLIER_SKU,
        "breadboard_product_url": ELECTRONICS_BACKBONE_BREADBOARD_PRODUCT_URL,
        "feather_pid": ELECTRONICS_BACKBONE_FEATHER_PID,
        "header_id": ELECTRONICS_BACKBONE_FEATHER_CONNECTION,
        "header_product_url": ELECTRONICS_BACKBONE_HEADER_PRODUCT_URL,
        "header_pin_counts": list(ELECTRONICS_BACKBONE_HEADER_PIN_COUNTS),
    }
    for key in (
        "breadboard_name",
        "breadboard_supplier_sku",
        "feather_pid",
        "header_id",
    ):
        _text(identity[key], f"intended_identity.{key}")
    _integer_list(identity["header_pin_counts"], "intended_identity.header_pin_counts")
    for key, expected_value in expected.items():
        if identity.get(key) != expected_value:
            raise ElectronicsBackboneEvidenceError(
                f"intended_identity.{key} does not match contract"
            )


def _validate_received_identity(value: Mapping[str, Any]) -> None:
    expected = {
        "breadboard_name": ELECTRONICS_BACKBONE_IDENTITY,
        "breadboard_supplier_sku": ELECTRONICS_BACKBONE_SUPPLIER_SKU,
        "feather_pid": ELECTRONICS_BACKBONE_FEATHER_PID,
        "header_id": ELECTRONICS_BACKBONE_FEATHER_CONNECTION,
        "header_pin_counts": list(ELECTRONICS_BACKBONE_HEADER_PIN_COUNTS),
    }
    for key in (
        "breadboard_name",
        "breadboard_supplier_sku",
        "feather_pid",
        "header_id",
    ):
        _text(value[key], f"received_identity.{key}")
    _integer_list(value["header_pin_counts"], "received_identity.header_pin_counts")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise ElectronicsBackboneEvidenceError(
                f"received_identity.{key} does not match contract"
            )


def _validate_topology(value: Any) -> None:
    topology = _mapping(value, "seller_topology")
    _require_exact_keys(topology, _TOPOLOGY_KEYS, "seller_topology")
    if (
        isinstance(topology.get("points"), bool)
        or not isinstance(topology.get("points"), int)
        or not isinstance(topology.get("rail_point_counts"), list)
        or not isinstance(topology.get("grid"), str)
        or type(topology.get("self_adhesive_rear")) is not bool
        or isinstance(topology.get("pitch_mm"), bool)
        or not isinstance(topology.get("pitch_mm"), (int, float))
    ):
        raise ElectronicsBackboneEvidenceError("seller_topology has invalid value types")
    _integer_list(topology["rail_point_counts"], "seller_topology.rail_point_counts")
    expected = {
        "points": ELECTRONICS_BACKBONE_TOPOLOGY_POINTS,
        "rail_point_counts": list(ELECTRONICS_BACKBONE_TOPOLOGY_RAIL_POINTS),
        "grid": ELECTRONICS_BACKBONE_TOPOLOGY_GRID,
        "pitch_mm": ELECTRONICS_BACKBONE_PITCH_MM,
        "self_adhesive_rear": True,
    }
    if any(topology.get(key) != expected_value for key, expected_value in expected.items()):
        raise ElectronicsBackboneEvidenceError("seller_topology does not match identity contract")


def _evidence_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ElectronicsBackboneEvidenceError("evidence_ids must be a non-empty bounded list")
    items = cast(list[Any], value)
    if not items or len(items) > _MAX_EVIDENCE_IDS:
        raise ElectronicsBackboneEvidenceError("evidence_ids must be a non-empty bounded list")
    values = tuple(_text(item, "evidence_ids entry") for item in items)
    if len(set(values)) != len(values):
        raise ElectronicsBackboneEvidenceError("evidence_ids must not contain duplicates")
    return values


def _integer_list(value: Any, label: str) -> None:
    if not isinstance(value, list):
        raise ElectronicsBackboneEvidenceError(f"{label} must be a list of integers")
    items = cast(list[Any], value)
    if any(isinstance(item, bool) or not isinstance(item, int) for item in items):
        raise ElectronicsBackboneEvidenceError(f"{label} must be a list of integers")


__all__ = [
    "ELECTRONICS_BACKBONE_BREADBOARD_PRODUCT_URL",
    "ELECTRONICS_BACKBONE_COORDINATE_FRAME",
    "ELECTRONICS_BACKBONE_FEATHER_PID",
    "ELECTRONICS_BACKBONE_GATE",
    "ELECTRONICS_BACKBONE_GRID_COLUMNS",
    "ELECTRONICS_BACKBONE_GRID_FRAME",
    "ELECTRONICS_BACKBONE_GRID_ROWS",
    "ELECTRONICS_BACKBONE_HEADER_PRODUCT_URL",
    "ELECTRONICS_BACKBONE_MIN_EXPECTED_COUNT",
    "ElectronicsBackboneEvidence",
    "ElectronicsBackboneEvidenceError",
    "load_electronics_backbone",
]
