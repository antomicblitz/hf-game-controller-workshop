"""Behavioral tests for the canonical case-local assembly scene."""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

_session = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_session / "tools"))

from cadkit.assembly import (  # noqa: E402
    CONTROL_COMPONENT_IDS,
    CONTROL_IDS,
    CONTROL_KINDS,
    CONTROL_ROLES,
    AssemblyScene,
    Dimensions,
    Transform,
    build123d_to_case_local,
    build_demo_assembly,
    case_local_to_build123d,
    local_to_scene_point,
    placements_from_assembly,
)
from cadkit.constraints import validate  # noqa: E402
from cadkit.manifest import ManifestSourceError, from_case_path  # noqa: E402
from cadkit.parametric import (  # noqa: E402
    BUTTON_ROUTE_MIN_BEND_RADIUS_PROVISIONAL_MM,
    FEATHER_BOARD_OUTLINE_MM,
    FEATHER_KEEP_OUT_MM,
    FEATHER_MOUNTING_HOLES_BR_MM,
    gamepad_body,
    snap_fit_pair,
)

from tests._pytest_helpers import approx  # noqa: E402


def _case_module():
    path = _session / "examples" / "6-button-gamepad" / "case.py"
    spec = importlib.util.spec_from_file_location("assembly_demo_case", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_case_local_transform_maps_center_and_round_trips_exactly():
    dimensions = Dimensions(130.0, 80.0, 35.5)

    assert case_local_to_build123d((65.0, 40.0, 0.0), dimensions) == (0.0, 0.0, 0.0)
    assert case_local_to_build123d((0.0, 0.0, 35.5), dimensions) == (-65.0, -40.0, 35.5)
    point = (110.0, 50.0, 25.5)
    assert build123d_to_case_local(
        case_local_to_build123d(point, dimensions), dimensions
    ) == approx(point, abs=1e-9)


def test_demo_scene_round_trips_deterministically_and_has_realistic_inventory():
    scene = build_demo_assembly()
    restored = AssemblyScene.from_dict(scene.to_dict())

    assert scene.to_json() == restored.to_json()
    ids = {node.id for node in scene.nodes}
    assert {
        "case.shell",
        "case.bottom",
        "case.top",
        "feather",
        "usb.connector",
        "usb.opening",
    } <= ids
    assert tuple(node.id for node in scene.nodes if node.mobility == "constrained_xy") == tuple(
        f"control.{control_id}" for control_id in CONTROL_IDS
    )
    assert not any(node.kind == "m2_5_fastener" for node in scene.nodes)
    assert scene.electronics_backbone is not None
    assert scene.electronics_backbone.status == "UNMEASURED"
    assert {route.signal for route in scene.routes} == {*CONTROL_ROLES, "GND"}
    assert not any(
        retired in repr(scene.to_dict()).lower()
        for retired in (
            "dpad",
            "qwiic",
            "i2c",
            "pca9554",
            "spare",
            "b1",
            "b2",
            "b3",
            "b4",
            "b5",
            "b6",
            "b7",
            "b8",
        )
    )


def test_demo_scene_exposes_case_fillet_to_every_2d_shell_layer() -> None:
    scene = build_demo_assembly(case_fillet_radius_mm=18.0)

    assert all(
        scene.node(node_id).visual["corner_radius_mm"] == 18.0
        for node_id in ("case.shell", "case.bottom", "case.top")
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", "cadkit.assembly.other"),
        ("schema_version", "0.1"),
        ("units", "inch"),
        ("coordinate_system", "build123d_origin"),
    ],
)
def test_assembly_scene_rejects_noncanonical_protocol_headers(field: str, value: str) -> None:
    scene = build_demo_assembly()
    payload = scene.to_dict()
    payload[field] = value

    with pytest.raises(ValueError):
        AssemblyScene.from_dict(payload)


def test_assembly_scene_rejects_visual_amplification_before_reconstruction() -> None:
    scene = build_demo_assembly()
    payload = scene.to_dict()
    feather = next(node for node in payload["nodes"] if node["id"] == "feather")
    feather["visual"]["headers"] = 1_000_000

    with pytest.raises(ValueError, match="count"):
        AssemblyScene.from_dict(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("headers", 1),
        ("mounting_holes_mm", 1),
        ("headers", [{"x_mm": 0.0, "y_mm": 0.0, "count": 12.0, "pitch_mm": 2.54, "axis": "x"}]),
        ("mounting_holes_mm", [[1.0]]),
    ],
)
def test_assembly_scene_rejects_non_list_frontend_visual_collections(
    field: str, value: object
) -> None:
    scene = build_demo_assembly()
    payload = scene.to_dict()
    feather = next(node for node in payload["nodes"] if node["id"] == "feather")
    feather["visual"][field] = value

    with pytest.raises(ValueError):
        AssemblyScene.from_dict(payload)


def test_scene_preserves_authoritative_feather_geometry_and_header_rows():
    scene = build_demo_assembly()
    feather = scene.node("feather")
    usb = scene.node("usb.connector")
    opening = scene.node("usb.opening")

    assert feather.physical_dimensions.x == FEATHER_BOARD_OUTLINE_MM[0]
    assert feather.physical_dimensions.y == FEATHER_BOARD_OUTLINE_MM[1]
    assert feather.keep_out_dimensions == Dimensions(*FEATHER_KEEP_OUT_MM)
    assert feather.transform.rotation_deg[2] == 180.0
    assert feather.visual["mounting_holes_native_mm"] == [
        list(hole) for hole in FEATHER_MOUNTING_HOLES_BR_MM
    ]
    assert len(feather.visual["mounting_holes_mm"]) == len(FEATHER_MOUNTING_HOLES_BR_MM) == 4
    assert {header["count"] for header in feather.visual["headers"]} == {12, 16}
    assert usb.transform.position == opening.transform.position
    assert usb.transform.position[0] == approx(3.0, abs=0.1)
    assert usb.transform.position[1] == approx(45.0, abs=0.1)
    assert usb.transform.position[2] == approx(17.75, abs=0.1)


def test_feather_frame_transforms_logical_headers_and_routes_once():
    scene = build_demo_assembly()

    assert local_to_scene_point(
        Transform((65.0, 50.0, 5.6), (0.0, 0.0, 90.0)), (27.0, 0.0, 12.15)
    ) == approx((65.0, 77.0, 17.75), abs=1e-9)
    assert scene.port_position("feather.usb_left") == approx((3.0, 45.0, 17.75), abs=1e-9)
    assert scene.port_position("feather.usb_left") == approx(
        scene.node("usb.connector").transform.position,
        abs=1e-9,
    )
    assert scene.node("feather.header_12").transform.rotation_deg[2] == 180.0
    assert scene.node("feather.header_16").transform.rotation_deg[2] == 180.0
    assert scene.route_signal_errors() == ()
    assert all(
        scene.route_waypoints(route)[0] == scene.port_position(route.source)
        for route in scene.routes
        if scene.port_position(route.source) is not None
    )
    assert {route.signal for route in scene.routes} == {*CONTROL_ROLES, "GND"}
    assert all(route.target.startswith("electronics.breadboard.") for route in scene.routes)
    assert {route.status for route in scene.routes} == {"logical"}
    assert {route.kind for route in scene.routes} == {"via-breadboard", "dupont-header-harness"}


def test_controls_are_ordered_constrained_and_component_specific():
    scene = build_demo_assembly()
    controls = [scene.node(f"control.{control_id}") for control_id in CONTROL_IDS]
    assert all(control.mobility == "constrained_xy" for control in controls)
    assert all(control.keep_out_dimensions is not None for control in controls)
    assert tuple(control.kind for control in controls) == CONTROL_KINDS
    assert tuple(control.metadata["control_role"] for control in controls) == CONTROL_ROLES
    assert tuple(control.metadata["component_id"] for control in controls) == CONTROL_COMPONENT_IDS
    button_xy = tuple(control.transform.position[:2] for control in controls)
    assert all(
        math.dist(left, right) >= 20.0
        for index, left in enumerate(button_xy)
        for right in button_xy[index + 1 :]
    )
    assert controls[-1].visual["shape"] != controls[0].visual["shape"]


def test_directional_cluster_is_left_of_both_action_controls() -> None:
    scene = build_demo_assembly()
    directional_x = [
        scene.node(f"control.{control_id}").transform.position[0] for control_id in CONTROL_IDS[:4]
    ]
    action_x = [
        scene.node(f"control.{control_id}").transform.position[0] for control_id in CONTROL_IDS[4:]
    ]

    assert max(directional_x) < min(action_x)


def test_control_provisional_metrics_are_component_specific_and_complete():
    scene = build_demo_assembly()
    controls = [scene.node(f"control.{control_id}") for control_id in CONTROL_IDS]
    placements = placements_from_assembly(scene)

    assert [control.metadata["cutout_diameter_mm"] for control in controls] == [
        12.4,
        12.4,
        12.4,
        12.4,
        12.4,
        12.4,
    ]
    assert placements.control_mounting_land_diameters_mm == (
        18.0,
        18.0,
        18.0,
        18.0,
        14.2,
        14.2,
    )
    assert placements.control_mounting_land_thicknesses_mm == (2.0,) * 6
    assert placements.control_ids == CONTROL_IDS
    assert placements.control_roles == CONTROL_ROLES
    assert placements.control_component_ids == CONTROL_COMPONENT_IDS
    assert placements.control_centres_xy == tuple(
        control.transform.position[:2] for control in controls
    )
    assert placements.control_cutout_diameters_mm == (12.4,) * 6
    assert placements.control_underside_keepouts_mm == (
        (18.0, 18.0, 10.69),
        (18.0, 18.0, 10.69),
        (18.0, 18.0, 10.69),
        (18.0, 18.0, 10.69),
        (14.2, 14.2, 21.5),
        (14.2, 14.2, 21.5),
    )
    assert placements.control_terminal_keepouts_mm == (
        (14.0, 5.83, 5.0),
        (14.0, 5.83, 5.0),
        (14.0, 5.83, 5.0),
        (14.0, 5.83, 5.0),
        (8.0, 4.0, 6.0),
        (8.0, 4.0, 6.0),
    )
    assert (
        placements.control_terminal_route_min_bend_radii_mm
        == (BUTTON_ROUTE_MIN_BEND_RADIUS_PROVISIONAL_MM,) * 6
    )
    assert placements.control_geometry_records == ()
    assert placements.button_centres_xy == ()
    assert placements.button_diameters_mm == ()
    assert placements.dpad_centre_xy is None
    assert len(placements.volumes) == 21
    assert not {"feather_cavity", "usb_cutout"} & {volume.name for volume in placements.volumes}
    assert len(placements.routes) == 15
    assert {route.min_bend_radius_mm for route in placements.routes} == {
        BUTTON_ROUTE_MIN_BEND_RADIUS_PROVISIONAL_MM,
        4.0,
    }

    violations = validate(gamepad_body(), placements=placements)
    assert {
        violation.rule
        for violation in violations
        if violation.severity == "warning" and violation.rule.endswith("_unverified")
    } == {"control_geometry_evidence_unverified", "electronics_backbone_unverified"}


def test_worked_case_uses_centered_geometry_and_cutouts_intersect_shell():
    module = _case_module()
    bbox = module.case.bounding_box()

    assert module.FILLET_RADIUS_MM == 6.0
    assert approx(-65.0, abs=1e-6) == bbox.min.X
    assert approx(65.0, abs=1e-6) == bbox.max.X
    assert approx(-45.0, abs=1e-6) == bbox.min.Y
    assert approx(45.0, abs=1e-6) == bbox.max.Y
    assert approx(0.0, abs=1e-6) == bbox.min.Z
    assert approx(65.0, abs=1e-6) == bbox.max.Z
    assert module.case.volume < 130.0 * 90.0 * 65.0 * 0.25
    assert module.ASSEMBLY_SPEC.node("usb.connector").transform.position == (3.0, 45.0, 17.75)
    assert module.ASSEMBLY_SPEC.node("case.bottom").physical_dimensions.z == 17.75
    assert module.ASSEMBLY_SPEC.node("case.top").physical_dimensions.z == 47.25
    opening = module.ASSEMBLY_SPEC.node("usb.opening")
    assert opening.transform.position[2] - opening.physical_dimensions.z / 2 < 17.75
    assert opening.transform.position[2] + opening.physical_dimensions.z / 2 > 17.75
    assert len(module.ASSEMBLY_SPEC.nodes) > 0

    dimensions = (
        module.CASE_LENGTH_MM,
        module.CASE_WIDTH_MM,
        module.CASE_THICKNESS_MM,
    )
    cavity_z = module.CASE_THICKNESS_MM / 2
    panel_z = module.CASE_THICKNESS_MM - 1.0
    for control in module.CONTROLS:
        position = module.ASSEMBLY_SPEC.node(f"control.{control['id']}").transform.position
        x, y, _ = case_local_to_build123d(position, dimensions)
        assert not module.case.is_inside((x, y, panel_z))
        assert not module.case.is_inside((x, y, cavity_z))

    top, bottom = snap_fit_pair(module.case)
    assert len(top.solids()) == 1
    assert len(bottom.solids()) == 1


def test_manifest_requires_canonical_assembly_artifact(tmp_path: Path) -> None:
    incomplete = tmp_path / "case.py"
    incomplete.write_text(
        "CASE_LENGTH_MM = 100.0\nCASE_WIDTH_MM = 60.0\nCASE_THICKNESS_MM = 30.0\nCONTROLS = []\n"
    )

    with pytest.raises(ManifestSourceError, match="canonical assembly artifact is required"):
        from_case_path(incomplete)
