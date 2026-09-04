"""Cross-subsystem acceptance contract for the active six-control design."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_session = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_session / "tools"))

from cadkit.assembly import (  # noqa: E402
    AssemblyScene,
    Dimensions,
    ElectronicsBackbone,
    build_demo_assembly,
    placements_from_assembly,
)
from cadkit.constraints import Placements  # noqa: E402
from firmware.gpio_map import CONTROL_IDS, CONTROL_ROLES, GPIO_NAMES_BY_CONTROL  # noqa: E402
from firmware.hid_report import (  # noqa: E402
    HID_AXIS_MAX,
    HID_AXIS_MIN,
    HID_AXIS_NEUTRAL,
    compute_hid_report,
)

from tests._pytest_helpers import approx  # noqa: E402

EXPECTED_IDS = ("up", "down", "right", "left", "action_a", "action_b")
EXPECTED_ROLES = ("UP", "DOWN", "RIGHT", "LEFT", "ACTION_A", "ACTION_B")
EXPECTED_NODES = tuple(f"control.{control_id}" for control_id in EXPECTED_IDS)
EXPECTED_COMPONENTS = (
    "pbs33b_directional",
    "pbs33b_directional",
    "pbs33b_directional",
    "pbs33b_directional",
    "guuzi_b09bmrdptn_action",
    "guuzi_b09bmrdptn_action",
)


def _states(*pressed: str) -> dict[str, bool]:
    return {control_id: control_id in pressed for control_id in EXPECTED_IDS}


def test_exact_six_identifiers_and_gpio_map_are_shared() -> None:
    assert CONTROL_IDS == EXPECTED_IDS
    assert CONTROL_ROLES == EXPECTED_ROLES
    assert GPIO_NAMES_BY_CONTROL == ("D5", "D6", "D9", "D10", "D11", "D12")


def test_canonical_assembly_contains_only_the_exact_six_controls() -> None:
    scene = build_demo_assembly()
    controls = tuple(node for node in scene.nodes if node.mobility == "constrained_xy")

    assert tuple(node.id for node in controls) == EXPECTED_NODES
    assert tuple(node.metadata["control_role"] for node in controls) == EXPECTED_ROLES
    assert tuple(node.metadata["component_id"] for node in controls) == EXPECTED_COMPONENTS
    assert tuple(node.kind for node in controls) == (
        "pbs33b_button",
        "pbs33b_button",
        "pbs33b_button",
        "pbs33b_button",
        "guuzi_action_button",
        "guuzi_action_button",
    )

    active_text = repr(scene.to_dict()).lower()
    for inactive_name in ("dpad", "qwiic", "i2c", "pca9554"):
        assert inactive_name not in active_text
    assert "m2_5" not in active_text

    placements = placements_from_assembly(scene)
    assert placements.control_ids == EXPECTED_IDS
    assert placements.control_roles == EXPECTED_ROLES
    assert placements.control_component_ids == EXPECTED_COMPONENTS
    assert len(placements.control_centres_xy) == 6
    assert len(placements.control_cutout_diameters_mm) == 6
    assert len(placements.control_mounting_land_diameters_mm) == 6
    assert len(placements.routes) == 15


def test_breadboard_backbone_is_identity_only_and_round_trips() -> None:
    scene = build_demo_assembly()
    backbone = scene.electronics_backbone
    assert backbone is not None
    assert backbone.identity == "The Pi Hut Half-Size Breadboard - White"
    assert backbone.supplier_sku == "100058"
    assert backbone.feather_connection == "ADA2830"
    assert backbone.header_pin_counts == (12, 16)
    assert backbone.topology.points == 400
    assert backbone.topology.rail_point_counts == (50, 50)
    assert backbone.topology.grid == "30x10"
    assert backbone.topology.pitch_mm == 2.54
    assert backbone.topology.self_adhesive_rear is True
    assert backbone.status == "UNMEASURED"
    assert backbone.retention_method == ("factory_adhesive_direct_to_inside_bottom_shell")
    assert backbone.outside_dimensions_mm is None
    assert backbone.stack_height_mm is None
    assert backbone.placement_offset_mm is None
    assert backbone.usb_offset_mm is None
    assert backbone.insertion_depth_mm is None
    assert backbone.adhesive_bond_interface is None
    assert backbone.adhesive_bond_verified is False
    assert backbone.signed_record_binding is None
    serialized_backbone = backbone.to_dict()
    assert serialized_backbone["retention_method"] == (
        "factory_adhesive_direct_to_inside_bottom_shell"
    )
    assert serialized_backbone["adhesive_bond_interface"] is None
    assert serialized_backbone["adhesive_bond_verified"] is False
    assert "retention_geometry" not in serialized_backbone
    assert "removal_result" not in serialized_backbone

    restored_scene = AssemblyScene.from_dict(scene.to_dict())
    assert restored_scene.to_json() == scene.to_json()

    placements = placements_from_assembly(scene)
    assert placements.electronics_backbone is not None
    assert placements.electronics_backbone.outside_dimensions_mm is None
    assert placements.electronics_backbone.stack_height_mm is None
    assert not any(volume.name == "electronics.breadboard" for volume in placements.volumes)
    restored_placements = Placements.from_dict(placements.to_dict())
    assert restored_placements.electronics_backbone == backbone
    assert restored_placements.to_json() == placements.to_json()


def test_breadboard_has_a_provisional_preview_without_freezing_geometry() -> None:
    scene = build_demo_assembly()
    breadboard = scene.node("electronics.breadboard")

    assert scene.case_dimensions == Dimensions(130.0, 90.0, 65.0)
    bottom = scene.node("case.bottom")
    top = scene.node("case.top")
    assert bottom.physical_dimensions.z == 17.75
    assert top.physical_dimensions.z == 47.25
    assert bottom.transform.position[2] == 8.875
    assert top.transform.position[2] == 41.375

    # The 17.75 mm limit is the global lower-shell cap.
    tall_scene = build_demo_assembly((130.0, 90.0, 98.0))
    assert tall_scene.node("case.bottom").physical_dimensions.z == 17.75
    assert tall_scene.node("case.top").physical_dimensions.z == 80.25

    assert breadboard.kind == "half_size_breadboard"
    assert breadboard.mobility == "fixed"
    assert breadboard.provenance == "operator-envelope-with-unmeasured-editor-placement"
    assert breadboard.status == "provisional"
    assert breadboard.transform.position == (45.0, 45.0, 10.0)
    assert breadboard.transform.rotation_deg == (0.0, 0.0, 0.0)
    assert breadboard.physical_dimensions.to_dict() == {
        "x_mm": 85.0,
        "y_mm": 55.0,
        "z_mm": 20.0,
    }
    assert breadboard.visual["shape"] == "half-size-breadboard"
    assert breadboard.visual["grid_columns"] == 30
    assert breadboard.visual["grid_rows"] == 10
    assert breadboard.metadata["supplier_sku"] == "100058"
    assert breadboard.metadata["mechanical_interface"] is False
    assert breadboard.metadata["dimension_status"] == (
        "operator_reported_preview_not_receiving_evidence"
    )
    assert breadboard.metadata["measurement_date"] == "2026-09-01"
    assert breadboard.metadata["measurement_method"] == "ruler"
    assert breadboard.metadata["measurement_scope"] == "installed_stack"
    assert breadboard.metadata["adhesive_included"] is True
    assert breadboard.metadata["photo_context"] == "top_side_unscaled"
    assert breadboard.metadata["receiving_evidence"] is False
    assert breadboard.metadata["placement_status"] == "unmeasured_editor_preview_only"
    assert breadboard.metadata["contact_layout_status"] == "provisional_logical_only"

    ports = {port.name: port for port in breadboard.ports}
    assert set(ports) == {
        *(f"signal_{control_id}" for control_id in EXPECTED_IDS),
        *(f"gnd_{control_id}" for control_id in EXPECTED_IDS),
        "gnd_bridge",
        "header_12_connector",
        "header_16_connector",
    }
    assert (
        tuple(ports[f"signal_{control_id}"].signal for control_id in EXPECTED_IDS) == EXPECTED_ROLES
    )
    assert all(ports[f"gnd_{control_id}"].signal == "GND" for control_id in EXPECTED_IDS)
    assert ports["gnd_bridge"].signal == "GND"
    assert all(
        ports[name].position[1] == approx(-breadboard.physical_dimensions.y * 0.41)
        for name in ports
        if name.startswith("gnd_")
    )

    backbone = scene.electronics_backbone
    assert backbone is not None
    assert backbone.status == "UNMEASURED"
    assert backbone.outside_dimensions_mm is None
    assert backbone.stack_height_mm is None
    assert backbone.placement_offset_mm is None

    feather = scene.node("feather")
    assert feather.metadata["orientation"] == "long_axis_-X_usb_facing_left"


def test_button_leads_terminate_on_provisional_breadboard_contacts() -> None:
    scene = build_demo_assembly()
    routes = {route.id: route for route in scene.routes}

    assert len(routes) == 15
    for control_id, role in zip(EXPECTED_IDS, EXPECTED_ROLES, strict=True):
        signal = routes[f"wire.control_{control_id}"]
        assert (signal.source, signal.target, signal.signal) == (
            f"control.{control_id}.signal",
            f"electronics.breadboard.signal_{control_id}",
            role,
        )
        ground = routes[f"wire.control_ground_{control_id}"]
        assert (ground.source, ground.target, ground.signal) == (
            f"control.{control_id}.ground",
            f"electronics.breadboard.gnd_{control_id}",
            "GND",
        )

    bridge = routes["wire.feather_ground_rail"]
    assert (bridge.source, bridge.target, bridge.signal) == (
        "feather.gnd",
        "electronics.breadboard.gnd_bridge",
        "GND",
    )
    for header_pin_count in (12, 16):
        harness = routes[f"wire.harness.header_{header_pin_count}"]
        assert (harness.source, harness.target, harness.signal) == (
            f"harness.header_{header_pin_count}.connector.connector",
            f"electronics.breadboard.header_{header_pin_count}_connector",
            "GND",
        )
    assert scene.route_signal_errors() == ()


def test_backbone_serialization_rejects_unknown_or_guessed_fields() -> None:
    payload = build_demo_assembly().to_dict()
    payload["electronics_backbone"]["unexpected_dimension_mm"] = 99.0
    with pytest.raises(ValueError, match="unknown or missing fields"):
        AssemblyScene.from_dict(payload)


def test_backbone_serialization_rejects_stale_retention_token() -> None:
    payload = build_demo_assembly().to_dict()
    payload["electronics_backbone"]["retention_method"] = (
        "removable_printed_locating_tray_with_side_rails_and_snap_tabs"
    )

    with pytest.raises(ValueError, match="active contract"):
        AssemblyScene.from_dict(payload)


def test_backbone_value_rejects_extra_or_non_direct_bond_interface_fields() -> None:
    with pytest.raises(ValueError, match="unknown or missing fields"):
        ElectronicsBackbone(
            adhesive_bond_interface={
                "surface": "inside_bottom_shell",
                "locator_geometry": {"enabled": True},
            }
        )


def test_six_active_low_controls_compose_semantic_hid_report() -> None:
    assert compute_hid_report(_states("up")) == (HID_AXIS_NEUTRAL, HID_AXIS_MIN, 0)
    assert compute_hid_report(_states("down")) == (HID_AXIS_NEUTRAL, HID_AXIS_MAX, 0)
    assert compute_hid_report(_states("right")) == (HID_AXIS_MAX, HID_AXIS_NEUTRAL, 0)
    assert compute_hid_report(_states("left")) == (HID_AXIS_MIN, HID_AXIS_NEUTRAL, 0)
    assert compute_hid_report(_states("action_a")) == (HID_AXIS_NEUTRAL, HID_AXIS_NEUTRAL, 1)
    assert compute_hid_report(_states("action_b")) == (HID_AXIS_NEUTRAL, HID_AXIS_NEUTRAL, 2)
    assert compute_hid_report(_states("action_a", "action_b")) == (
        HID_AXIS_NEUTRAL,
        HID_AXIS_NEUTRAL,
        3,
    )
    assert compute_hid_report(_states("up", "down", "left", "right")) == (
        HID_AXIS_NEUTRAL,
        HID_AXIS_NEUTRAL,
        0,
    )
