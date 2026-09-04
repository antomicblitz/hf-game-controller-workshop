"""Focused tests for the exact-six placement and constraint contract."""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import cast

_session = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_session / "tools"))

import pytest  # noqa: E402
from build123d import (  # noqa: E402
    Align,
    Box,
    BuildPart,
    Cylinder,
    Locations,
    Mode,
    Part,
    add,  # pyright: ignore[reportUnknownVariableType]
)
from cadkit.aabb import AABB, Route, Volume  # noqa: E402
from cadkit.assembly import ElectronicsBackbone  # noqa: E402
from cadkit.constraints import (  # noqa: E402
    AVAILABLE_MATERIALS,
    CASE_MAX_MM,
    MAX_BUTTON_COUNT,
    MIN_BUTTON_COUNT,
    ConstraintViolation,
    Placements,
    is_printable,
    validate,
)
from cadkit.electronics_backbone import load_electronics_backbone  # noqa: E402
from cadkit.evidence import write_test_electronics_backbone_record  # noqa: E402
from cadkit.parametric import (  # noqa: E402
    ACTIVE_CONTROL_COMPONENT_IDS,
    GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID,
    PBS33B_DIRECTIONAL_COMPONENT_ID,
    FrozenControlGeometry,
    gamepad_body,
    snap_fit_pair,
)

from tests._cad_helpers import synthetic_tall_placements  # noqa: E402

CONTROL_IDS = ("up", "down", "right", "left", "action_a", "action_b")
CONTROL_ROLES = ("UP", "DOWN", "RIGHT", "LEFT", "ACTION_A", "ACTION_B")


def _signed_geometry(component_id: str) -> FrozenControlGeometry:
    pbs = component_id == PBS33B_DIRECTIONAL_COMPONENT_ID
    return FrozenControlGeometry(
        component_id=component_id,
        cutout_diameter_mm=12.4,
        mounting_land_diameter_mm=18.0 if pbs else 14.2,
        mounting_land_thickness_mm=2.0,
        underside_keepout_mm=(18.0, 18.0, 10.69) if pbs else (14.2, 14.2, 21.5),
        terminal_keepout_mm=(14.0, 5.83, 5.0) if pbs else (8.0, 4.0, 6.0),
        source="test-receiving-record",
        signed_by="Test signer",
        signed_utc="2026-08-28T00:00:00Z",
        signed_receiving_row=f"{component_id}-row",
        coupon_binding=f"{component_id}-coupon",
        minimum_thread_length_mm=4.0 if pbs else 7.5,
        terminal_route_min_bend_radius_mm=10.0,
    )


def _active_placements(
    *,
    components: tuple[str, ...] = ACTIVE_CONTROL_COMPONENT_IDS,
    records: tuple[FrozenControlGeometry, ...] | None = None,
) -> Placements:
    placements = synthetic_tall_placements()
    return dataclasses.replace(
        placements,
        control_component_ids=components,
        control_geometry_records=(
            records
            if records is not None
            else tuple(_signed_geometry(component) for component in components)
        ),
    )


def _body(*, cutout_start_z: float = 94.0, cutout_height: float = 4.0) -> Part:
    placements = _active_placements()
    body = gamepad_body(thickness_mm=98.0, fillet_radius_mm=0.0)
    with BuildPart() as bp:
        add(body)
        for (x, y), diameter in zip(
            placements.control_centres_xy,
            placements.control_cutout_diameters_mm,
            strict=True,
        ):
            with Locations(
                (
                    x - placements.case_length_mm / 2,
                    y - placements.case_width_mm / 2,
                    cutout_start_z,
                )
            ):
                Cylinder(
                    diameter / 2,
                    cutout_height,
                    align=(Align.CENTER, Align.CENTER, Align.MIN),
                    mode=Mode.SUBTRACT,
                )
    return cast(Part, bp.part)


def _solid_body() -> Part:
    with BuildPart() as bp:
        Box(130.0, 90.0, 98.0, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return cast(Part, bp.part)


def _frozen_backbone() -> ElectronicsBackbone:
    """Synthetic frozen values test validation only, not physical evidence."""
    return ElectronicsBackbone(
        status="FROZEN",
        outside_dimensions_mm=(100.0, 50.0, 10.0),
        stack_height_mm=12.0,
        placement_offset_mm=(0.0, 0.0, 0.0),
        usb_offset_mm=(1.0, 2.0, 3.0),
        insertion_depth_mm=4.0,
        adhesive_bond_interface={
            "surface": "inside_bottom_shell",
        },
        adhesive_bond_verified=True,
        signed_record_binding="synthetic-test-record",
    )


def test_active_materials_and_count_are_exactly_six() -> None:
    assert MIN_BUTTON_COUNT == 6
    assert MAX_BUTTON_COUNT == 6
    assert AVAILABLE_MATERIALS["buttons"] == {
        PBS33B_DIRECTIONAL_COMPONENT_ID,
        GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID,
    }
    assert "dpad" not in AVAILABLE_MATERIALS
    assert "qwiic" not in AVAILABLE_MATERIALS


def test_validator_rejects_solid_case_without_component_space() -> None:
    violations = validate(_solid_body(), placements=_active_placements())
    rules = {violation.rule for violation in violations}

    assert "case_interior_cavity_missing" in rules
    assert "control_opening_not_through" in rules

    short_slab = Box(130.0, 90.0, 97.9, align=(Align.CENTER, Align.CENTER, Align.MIN))
    assert _active_placements().case_thickness_mm != short_slab.bounding_box().size.Z
    assert "case_interior_cavity_missing" in {
        violation.rule for violation in validate(short_slab, placements=_active_placements())
    }


def test_validator_requires_all_six_panel_openings_to_reach_cavity() -> None:
    uncut_shell = gamepad_body(thickness_mm=98.0, fillet_radius_mm=0.0)
    partly_cut_shell = _body(cutout_start_z=96.4, cutout_height=1.1)
    blocked = validate(uncut_shell, placements=_active_placements())
    partly_blocked = validate(partly_cut_shell, placements=_active_placements())
    valid = validate(_body(), placements=_active_placements())

    assert sum(violation.rule == "control_opening_not_through" for violation in blocked) == 6
    assert not partly_cut_shell.is_inside((-30.0, -25.0, 96.5))
    assert partly_cut_shell.is_inside((-30.0, -25.0, 96.0))
    assert sum(violation.rule == "control_opening_not_through" for violation in partly_blocked) == 6
    assert not any(violation.rule == "control_opening_not_through" for violation in valid)
    assert not any(violation.rule == "case_interior_cavity_missing" for violation in valid)

    for half in snap_fit_pair(uncut_shell):
        half_violations = validate(half, placements=_active_placements())
        assert not any(
            violation.rule == "control_opening_not_through" for violation in half_violations
        )


def test_active_placements_preserve_ids_roles_components_and_geometry() -> None:
    placements = _active_placements()
    assert placements.control_ids == CONTROL_IDS
    assert placements.control_roles == CONTROL_ROLES
    assert placements.control_component_ids == ACTIVE_CONTROL_COMPONENT_IDS
    assert len(placements.control_centres_xy) == 6
    assert placements.control_cutout_diameters_mm[-1] == 12.4
    assert placements.control_geometry_records[-1].component_id == (
        GUUZI_B09BMRDPTN_ACTION_COMPONENT_ID
    )


def test_backbone_gate_rejects_missing_provisional_and_mismatched_records() -> None:
    missing = validate(_body(), placements=_active_placements(), require_export_verification=True)
    assert any(
        violation.rule == "electronics_backbone_unverified" and violation.severity == "error"
        for violation in missing
    )

    provisional = dataclasses.replace(
        _active_placements(), electronics_backbone=ElectronicsBackbone()
    )
    provisional_violations = validate(
        _body(), placements=provisional, require_export_verification=True
    )
    assert any(
        violation.rule == "electronics_backbone_unverified" and violation.severity == "error"
        for violation in provisional_violations
    )

    mismatched = dataclasses.replace(
        _active_placements(),
        electronics_backbone=dataclasses.replace(_frozen_backbone(), supplier_sku="wrong"),
    )
    mismatched_violations = validate(
        _body(), placements=mismatched, require_export_verification=True
    )
    assert any(
        violation.rule == "electronics_backbone_identity_mismatch" and violation.severity == "error"
        for violation in mismatched_violations
    )


def test_source_authored_frozen_backbone_cannot_pass_its_specific_gate() -> None:
    placements = dataclasses.replace(_active_placements(), electronics_backbone=_frozen_backbone())
    violations = validate(_body(), placements=placements, require_export_verification=True)
    assert "electronics_backbone_evidence_unverified" in {
        violation.rule for violation in violations
    }


def test_actual_frozen_backbone_record_passes_its_specific_gate(tmp_path: Path) -> None:
    record = write_test_electronics_backbone_record(
        tmp_path / "backbone.json",
        id_slug="backbone",
    )
    evidence = load_electronics_backbone(record)
    placements = dataclasses.replace(
        _active_placements(),
        electronics_backbone=evidence.to_electronics_backbone(),
    )

    violations = validate(
        _body(),
        placements=placements,
        require_export_verification=True,
        electronics_backbone_evidence=evidence,
    )

    assert not any(violation.rule.startswith("electronics_backbone") for violation in violations)


def test_active_placements_export_neutralizes_historical_control_fields() -> None:
    placements = dataclasses.replace(
        _active_placements(),
        dpad_centre_xy=(100.0, 40.0),
        dpad_cable_corridor_mm=(12.0, 8.0, 10.0),
    )
    payload = json.loads(placements.to_json())
    restored = Placements.from_dict(payload)
    assert restored.to_dict() == payload
    assert restored.is_active_control_contract
    assert payload["schema"] == "cadkit.placements"
    assert payload["schema_version"] == "1.0"
    assert set(payload) == {
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
    assert payload["dpad_centre_xy"] is None
    assert payload["dpad_cable_corridor_mm"] is None
    assert "button_centres_xy" not in payload


def test_bounded_historical_artifact_parses_without_becoming_active() -> None:
    historical = Placements(
        button_centres_xy=((20.0, 20.0),),
        button_diameters_mm=(12.0,),
        dpad_centre_xy=(100.0, 40.0),
        dpad_cable_corridor_mm=(12.0, 8.0, 10.0),
        volumes=(Volume("old", AABB(10, 10, 0, 20, 20, 4)),),
        routes=(Route("old-wire", ((10, 10, 1), (20, 10, 1)), 1.0),),
    )
    payload = historical.to_dict()
    for key in tuple(payload):
        if key.startswith("control_"):
            del payload[key]
    restored = Placements.from_dict(payload)
    assert not restored.is_active_control_contract
    assert restored.dpad_cable_corridor_mm == (12.0, 8.0, 10.0)
    assert restored.to_dict() == payload
    violations = validate(_body(), placements=restored)
    assert "legacy_control_contract" in {violation.rule for violation in violations}
    assert not any("dpad" in violation.rule for violation in violations)


def test_active_validator_rejects_wrong_ids_and_component_mix() -> None:
    wrong_ids = dataclasses.replace(_active_placements(), control_ids=("a",) * 5)
    wrong_components = _active_placements(components=(PBS33B_DIRECTIONAL_COMPONENT_ID,) * 5)
    assert "control_ids_exact_six" in {
        violation.rule for violation in validate(_body(), placements=wrong_ids)
    }
    assert "control_component_mix" in {
        violation.rule for violation in validate(_body(), placements=wrong_components)
    }


def test_active_validator_rejects_exact_five_artifacts() -> None:
    six = _active_placements()
    five = dataclasses.replace(
        six,
        control_ids=six.control_ids[:-1],
        control_roles=six.control_roles[:-1],
        control_component_ids=six.control_component_ids[:-1],
        control_centres_xy=six.control_centres_xy[:-1],
        control_cutout_diameters_mm=six.control_cutout_diameters_mm[:-1],
        control_mounting_land_diameters_mm=six.control_mounting_land_diameters_mm[:-1],
        control_mounting_land_thicknesses_mm=six.control_mounting_land_thicknesses_mm[:-1],
        control_underside_keepouts_mm=six.control_underside_keepouts_mm[:-1],
        control_terminal_keepouts_mm=six.control_terminal_keepouts_mm[:-1],
        control_terminal_route_min_bend_radii_mm=six.control_terminal_route_min_bend_radii_mm[:-1],
        control_geometry_records=six.control_geometry_records[:-1],
    )

    restored = Placements.from_dict(json.loads(five.to_json()))
    rules = {violation.rule for violation in validate(_body(), placements=restored)}
    assert "control_ids_exact_six" in rules
    assert "min_button_count" in rules
    assert "control_centres_xy_unverified" in rules


def test_active_validator_ignores_historical_dpad_scalar() -> None:
    placements = dataclasses.replace(_active_placements(), dpad_centre_xy=(1.0, 1.0))
    violations = validate(_body(), placements=placements)
    rules = {violation.rule for violation in violations}
    assert not any("dpad" in rule for rule in rules)
    assert "control_ids_exact_six" not in rules


def test_active_validator_preserves_signed_evidence_fail_closed() -> None:
    placements = dataclasses.replace(_active_placements(), control_geometry_records=())
    violations = validate(_body(), placements=placements, require_export_verification=True)
    assert "control_geometry_evidence_unverified" in {violation.rule for violation in violations}
    assert not is_printable(
        _body(),
        placements=placements,
        overhang_verified=True,
        bridge_verified=True,
        supports_used=False,
    )


def test_production_rejects_empty_or_historical_control_placements() -> None:
    violations = validate(
        _body(),
        placements=Placements(),
        require_export_verification=True,
        overhang_verified=True,
        bridge_verified=True,
        supports_used=False,
    )
    assert "exact_six_controls_unverified" in {violation.rule for violation in violations}


def test_production_requires_exactly_two_button_geometry_records() -> None:
    placements = _active_placements()
    violations = validate(
        _body(),
        placements=placements,
        require_export_verification=True,
        overhang_verified=True,
        bridge_verified=True,
        supports_used=False,
    )
    assert "control_geometry_evidence_unverified" in {violation.rule for violation in violations}


def test_active_export_requires_all_per_control_routes_without_corridor_contract() -> None:
    routes = (
        *(
            Route(
                f"wire.control_{control_id}",
                ((10.0 + index * 20.0, 10.0, 1.0), (11.0 + index * 20.0, 10.0, 1.0)),
                1.0,
            )
            for index, control_id in enumerate(CONTROL_IDS)
        ),
        *(
            Route(
                f"wire.control_ground_{control_id}",
                ((10.0 + index * 20.0, 12.0, 1.0), (11.0 + index * 20.0, 12.0, 1.0)),
                1.0,
            )
            for index, control_id in enumerate(CONTROL_IDS)
        ),
    )
    placements = dataclasses.replace(_active_placements(), routes=routes)
    violations = validate(
        _body(),
        placements=placements,
        require_export_verification=True,
        overhang_verified=True,
        bridge_verified=True,
        supports_used=False,
    )
    rules = {violation.rule for violation in violations}
    assert "protected_routes_unverified" not in rules
    assert not any("corridor" in rule or "dpad" in rule for rule in rules)


def test_constraint_violation_to_dict_is_stable() -> None:
    violation = ConstraintViolation("print", "test", "warning", "message")
    assert violation.to_dict() == {
        "category": "print",
        "rule": "test",
        "severity": "warning",
        "message": "message",
        "location": "",
    }


def test_case_envelope_remains_fail_closed_at_export() -> None:
    with BuildPart() as bp:
        Box(CASE_MAX_MM[0] + 1.0, CASE_MAX_MM[1] + 1.0, CASE_MAX_MM[2] + 1.0)
    violations = validate(cast(Part, bp.part), require_export_verification=True)
    assert any(violation.rule == "case_envelope" for violation in violations)


def test_case_envelope_tolerates_brep_round_trip_noise() -> None:
    with BuildPart() as bp:
        Box(CASE_MAX_MM[0], CASE_MAX_MM[1] + 5e-8, CASE_MAX_MM[2])
    rules = {
        violation.rule
        for violation in validate(cast(Part, bp.part), require_export_verification=True)
    }

    assert "case_envelope" not in rules
    assert "max_case_width_mm" not in rules


@pytest.mark.parametrize("field", ["control_ids", "control_roles", "control_component_ids"])
def test_canonical_payload_rejects_unknown_keys(field: str) -> None:
    payload = json.loads(_active_placements().to_json())
    payload["unexpected"] = field
    with pytest.raises(ValueError, match="unknown or missing fields"):
        Placements.from_dict(payload)
